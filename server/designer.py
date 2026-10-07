"""Live design calculations behind the web UI. No CadQuery: runs on Vercel.

The UI sends every input on each change; ``evaluate`` turns them into a blank
outline, press forces, strip layouts, the plate stack and rule results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from engine.calc.forces import (
    CuttingContour,
    PressForces,
    circle_contour,
    polyline_contour,
    press_forces,
)
from engine.calc.strip_layout import StripLayoutOptions, polygon_area_mm2, strip_layout_options
from engine.stack import Stack, ToolSpec
from rules.engine import RuleResult, RulesEngine, Subject
from rules.standards import Standards, StandardsError

Shape = Literal["rectangle", "circle", "custom"]
ARC_STEP_DEG = 5.0


class DesignInput(BaseModel):
    """Everything the designer can change in the UI, with example starting values."""

    # Part and material
    shape: Shape = "rectangle"
    length_mm: float = Field(60.0, gt=0)
    width_mm: float = Field(40.0, gt=0)
    corner_radius_mm: float = Field(4.0, ge=0)
    diameter_mm: float = Field(50.0, gt=0)
    custom_points: str = "0,0\n60,0\n60,25\n35,25\n35,40\n0,40"
    holes: str = "8, 15, 20\n8, 45, 20"
    material: str = "mild_steel"
    thickness_mm: float = Field(1.5, gt=0)
    press_id: str = "PR-400"
    # Tool stack
    die_set_id: str = "DS-250x200"
    plate_length_mm: float = Field(160.0, gt=0)
    plate_width_mm: float = Field(120.0, gt=0)
    die_plate_thickness_mm: float = Field(25.0, gt=0)
    stripper_gap_mm: float = Field(2.0, gt=0)
    stripper_thickness_mm: float = Field(12.0, gt=0)
    punch_holder_thickness_mm: float = Field(20.0, gt=0)
    back_plate_thickness_mm: float = Field(6.0, gt=0)
    punch_length_mm: float = Field(80.0, gt=0)
    die_penetration_mm: float = Field(1.0, ge=0)


@dataclass(frozen=True)
class Hole:
    diameter_mm: float
    x_mm: float
    y_mm: float


@dataclass
class DesignResult:
    inputs: DesignInput
    errors: list[str] = field(default_factory=list)
    outline: list[tuple[float, float]] = field(default_factory=list)
    holes: list[Hole] = field(default_factory=list)
    blank_area_mm2: float = 0.0
    min_edge_distance_mm: float | None = None
    min_web_mm: float | None = None
    forces: PressForces | None = None
    layouts: StripLayoutOptions | None = None
    stack: Stack | None = None
    die_set: dict[str, float] = field(default_factory=dict)
    rules: list[RuleResult] = field(default_factory=list)
    placeholder_tables: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return bool(self.errors) or any(r.status == "fail" for r in self.rules)


def parse_form(form: dict[str, str]) -> tuple[DesignInput, list[str]]:
    """Inputs from a submitted form; bad fields fall back to their defaults, with an error."""
    data = {k: v for k, v in form.items() if k in DesignInput.model_fields}
    errors: list[str] = []
    try:
        return DesignInput.model_validate(data), errors
    except ValidationError as error:
        bad = {str(e["loc"][0]) for e in error.errors()}
        for e in error.errors():
            errors.append(f"{e['loc'][0]}: {e['msg']}")
        cleaned = {k: v for k, v in data.items() if k not in bad}
        return DesignInput.model_validate(cleaned), errors


def evaluate(inputs: DesignInput, standards: Standards, rules: RulesEngine) -> DesignResult:
    result = DesignResult(inputs=inputs, placeholder_tables=standards.placeholder_tables)

    try:
        result.outline = blank_outline(inputs)
    except ValueError as error:
        result.errors.append(str(error))
        return result
    try:
        result.holes = parse_holes(inputs.holes)
    except ValueError as error:
        result.errors.append(str(error))

    for i, hole in enumerate(result.holes, start=1):
        if not _inside(result.outline, (hole.x_mm, hole.y_mm)):
            result.errors.append(f"Hole {i} centre ({hole.x_mm}, {hole.y_mm}) is outside the blank")
    holes_area = sum(math.pi * h.diameter_mm**2 / 4 for h in result.holes)
    result.blank_area_mm2 = polygon_area_mm2(result.outline) - holes_area
    if result.holes and not result.errors:
        result.min_edge_distance_mm = min(
            _distance_to_outline(result.outline, (h.x_mm, h.y_mm)) - h.diameter_mm / 2
            for h in result.holes
        )
        if len(result.holes) > 1:
            result.min_web_mm = min(
                math.dist((a.x_mm, a.y_mm), (b.x_mm, b.y_mm)) - (a.diameter_mm + b.diameter_mm) / 2
                for i, a in enumerate(result.holes)
                for b in result.holes[i + 1 :]
            )

    subjects: list[Subject] = []
    try:
        contours: list[CuttingContour] = [polyline_contour("BLANK", result.outline)]
        contours += [
            circle_contour(f"H{i}", h.diameter_mm, h.x_mm, h.y_mm)
            for i, h in enumerate(result.holes, start=1)
        ]
        result.forces = press_forces(contours, inputs.thickness_mm, inputs.material, standards)
        result.layouts = strip_layout_options(
            result.outline, inputs.thickness_mm, standards, result.blank_area_mm2
        )
        subjects.append(
            Subject(
                kind="press_load",
                id="PRESS",
                values={
                    "press_id": inputs.press_id,
                    "press_force_kn": result.forces.press_force_kn,
                },
            )
        )
        subjects.append(
            Subject(
                kind="strip_layout",
                id="STRIP",
                values={
                    "material": inputs.material,
                    "thickness_mm": inputs.thickness_mm,
                    "min_web_mm": result.layouts.best.bridge_mm,
                },
            )
        )
    except StandardsError as error:
        result.errors.append(str(error))

    base = {"material": inputs.material, "thickness_mm": inputs.thickness_mm}
    subjects.append(Subject(kind="blank_punch", id="BLANK", values=dict(base)))
    for i, h in enumerate(result.holes, start=1):
        subjects.append(
            Subject(kind="pierce_punch", id=f"H{i}", values={**base, "diameter_mm": h.diameter_mm})
        )
    if result.min_edge_distance_mm is not None:
        subjects.append(
            Subject(
                kind="part",
                id="PART",
                values={**base, "min_edge_distance_mm": result.min_edge_distance_mm},
            )
        )
    if result.min_web_mm is not None:
        subjects.append(
            Subject(kind="part_web", id="PART", values={**base, "min_web_mm": result.min_web_mm})
        )

    try:
        row = standards.lookup("die_sets", die_set_id=inputs.die_set_id)
        result.die_set = {k: float(v) for k, v in row.items() if not isinstance(v, str)}
        spec = ToolSpec(
            material=inputs.material,
            sheet_thickness_mm=inputs.thickness_mm,
            press_id=inputs.press_id,
            die_set_id=inputs.die_set_id,
            die_plate_thickness_mm=inputs.die_plate_thickness_mm,
            stripper_gap_mm=inputs.stripper_gap_mm,
            stripper_thickness_mm=inputs.stripper_thickness_mm,
            punch_holder_thickness_mm=inputs.punch_holder_thickness_mm,
            back_plate_thickness_mm=inputs.back_plate_thickness_mm,
            punch_length_mm=inputs.punch_length_mm,
            die_penetration_mm=inputs.die_penetration_mm,
            stripper_clearance_per_side_mm=1.0,  # not used by the stack
            slug_relief_per_side_mm=1.0,  # not used by the stack
        )
        result.stack = Stack.from_spec(
            spec,
            result.die_set["lower_shoe_thickness_mm"],
            result.die_set["upper_shoe_thickness_mm"],
        )
        subjects.append(
            Subject(
                kind="tool",
                id="TOOL",
                values={"press_id": inputs.press_id, "shut_height_mm": result.stack.shut_height_mm},
            )
        )
        if result.stack.holder_bottom < result.stack.stripper_top:
            result.errors.append(
                f"Punch holder bottom ({result.stack.holder_bottom:.1f} mm) is below the stripper "
                f"top ({result.stack.stripper_top:.1f} mm) when closed: lengthen the punches or "
                "thin the plates"
            )
        if inputs.die_penetration_mm >= inputs.die_plate_thickness_mm:
            result.errors.append("Die penetration is deeper than the die plate")
    except (StandardsError, ValidationError) as error:
        result.errors.append(str(error))

    result.rules = rules.check(subjects)
    return result


# --- blank geometry -------------------------------------------------------------------


def blank_outline(inputs: DesignInput) -> list[tuple[float, float]]:
    """The blank outline as a closed polyline (first point not repeated), lower-left at 0, 0."""
    if inputs.shape == "circle":
        r = inputs.diameter_mm / 2
        steps = int(360 / ARC_STEP_DEG)
        return [
            (
                r + r * math.cos(math.radians(i * ARC_STEP_DEG)),
                r + r * math.sin(math.radians(i * ARC_STEP_DEG)),
            )
            for i in range(steps)
        ]
    if inputs.shape == "custom":
        points = _parse_rows(inputs.custom_points, 2, "outline point (x, y)")
        if len(points) < 3:
            raise ValueError("The custom outline needs at least 3 points")
        if _signed_area(points) == 0:
            raise ValueError("The custom outline has no area")
        return [(p[0], p[1]) for p in points]
    return _rounded_rectangle(inputs.length_mm, inputs.width_mm, inputs.corner_radius_mm)


def parse_holes(text: str) -> list[Hole]:
    rows = _parse_rows(text, 3, "hole (diameter, x, y)")
    holes = [Hole(diameter_mm=r[0], x_mm=r[1], y_mm=r[2]) for r in rows]
    for i, h in enumerate(holes, start=1):
        if h.diameter_mm <= 0:
            raise ValueError(f"Hole {i}: diameter must be more than 0")
    return holes


def _parse_rows(text: str, columns: int, what: str) -> list[list[float]]:
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [p for p in line.replace(";", ",").replace(" ", ",").split(",") if p]
        try:
            values = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"Line {n}: '{line}' is not a {what}") from None
        if len(values) != columns:
            raise ValueError(f"Line {n}: '{line}' should be a {what}")
        rows.append(values)
    return rows


def _rounded_rectangle(length: float, width: float, radius: float) -> list[tuple[float, float]]:
    radius = min(radius, length / 2 - 1e-6, width / 2 - 1e-6)
    if radius <= 0:
        return [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]
    corners = [
        (length - radius, radius, -90.0),
        (length - radius, width - radius, 0.0),
        (radius, width - radius, 90.0),
        (radius, radius, 180.0),
    ]
    points: list[tuple[float, float]] = []
    steps = int(90 / ARC_STEP_DEG)
    for cx, cy, start in corners:
        for i in range(steps + 1):
            a = math.radians(start + i * ARC_STEP_DEG)
            points.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return points


def _signed_area(points: list[list[float]]) -> float:
    return (
        sum(
            points[i][0] * points[(i + 1) % len(points)][1]
            - points[(i + 1) % len(points)][0] * points[i][1]
            for i in range(len(points))
        )
        / 2
    )


def _inside(polygon: list[tuple[float, float]], point: tuple[float, float]) -> bool:
    x, y = point
    inside = False
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _distance_to_outline(polygon: list[tuple[float, float]], point: tuple[float, float]) -> float:
    best = math.inf
    px, py = point
    for i in range(len(polygon)):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % len(polygon)]
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        t = (
            0.0
            if length_sq == 0
            else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        )
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return best
