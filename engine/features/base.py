"""Shared types for parametric tool features.

Coordinates: the strip lies on the die plate top face at z = 0 and feeds along +X.
Z points up. The tool is modelled in the closed (shut) position, at the bottom of
the press stroke, so clash and shut height checks see the tightest state.

Stack in the closed position, bottom to top::

    lower shoe      [die_bottom - lower_shoe, die_bottom]
    die plate       [-die_plate_thickness, 0]
    strip           [0, sheet_thickness]
    stripper plate  [sheet_thickness + stripper_gap, ... + stripper_thickness]
    punch tips      at -die_penetration
    punch holder    [holder_top - punch_holder_thickness, holder_top],
                    holder_top = -die_penetration + punch_length
    back plate      [holder_top, holder_top + back_plate_thickness]
    upper shoe      [back_plate top, + upper_shoe]

Every value is an input from the designer or a standards table; nothing here has a
default tool-design value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cadquery as cq
from pydantic import BaseModel, Field

from rules.standards import Standards


class ToolSpec(BaseModel):
    """The first feature of every tool: material, press and the plate stack."""

    material: str
    sheet_thickness_mm: float = Field(gt=0)
    press_id: str
    die_set_id: str
    die_plate_thickness_mm: float = Field(gt=0)
    stripper_gap_mm: float = Field(gt=0, description="Strip top to stripper bottom")
    stripper_thickness_mm: float = Field(gt=0)
    punch_holder_thickness_mm: float = Field(gt=0)
    back_plate_thickness_mm: float = Field(gt=0)
    punch_length_mm: float = Field(gt=0)
    die_penetration_mm: float = Field(ge=0)
    stripper_clearance_per_side_mm: float = Field(gt=0)
    slug_relief_per_side_mm: float = Field(gt=0)


class Point(BaseModel):
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class Stack:
    """Z levels of the closed tool, derived from the ToolSpec and the die set."""

    sheet_thickness: float
    die_bottom: float
    stripper_bottom: float
    stripper_top: float
    punch_tip: float
    holder_bottom: float
    holder_top: float
    back_top: float
    lower_shoe_bottom: float
    upper_shoe_top: float

    @classmethod
    def from_spec(
        cls, spec: ToolSpec, lower_shoe_thickness_mm: float, upper_shoe_thickness_mm: float
    ) -> Stack:
        punch_tip = -spec.die_penetration_mm
        holder_top = punch_tip + spec.punch_length_mm
        back_top = holder_top + spec.back_plate_thickness_mm
        stripper_bottom = spec.sheet_thickness_mm + spec.stripper_gap_mm
        return cls(
            sheet_thickness=spec.sheet_thickness_mm,
            die_bottom=-spec.die_plate_thickness_mm,
            stripper_bottom=stripper_bottom,
            stripper_top=stripper_bottom + spec.stripper_thickness_mm,
            punch_tip=punch_tip,
            holder_bottom=holder_top - spec.punch_holder_thickness_mm,
            holder_top=holder_top,
            back_top=back_top,
            lower_shoe_bottom=-spec.die_plate_thickness_mm - lower_shoe_thickness_mm,
            upper_shoe_top=back_top + upper_shoe_thickness_mm,
        )

    @property
    def shut_height_mm(self) -> float:
        return self.upper_shoe_top - self.lower_shoe_bottom


@dataclass
class Cut:
    """Material to remove from another component (a hole, pocket or opening)."""

    target_id: str
    solid: cq.Shape


@dataclass
class Component:
    id: str
    kind: str
    solid: cq.Shape
    metadata: dict[str, Any] = field(default_factory=dict)
    cuts: list[Cut] = field(default_factory=list)


@dataclass
class BuildContext:
    spec: ToolSpec
    stack: Stack
    standards: Standards

    @property
    def die_clearance_per_side_mm(self) -> float:
        """Punch-die clearance per side, from the standards clearance table."""
        pct = self.standards.value(
            "clearance_pct_by_material",
            "clearance_pct",
            material=self.spec.material,
            thickness_mm=self.spec.sheet_thickness_mm,
        )
        return pct / 100.0 * self.spec.sheet_thickness_mm


# --- solid helpers --------------------------------------------------------------------


def box(
    length_mm: float, width_mm: float, x_mm: float, y_mm: float, z_bottom: float, z_top: float
) -> cq.Shape:
    """Axis-aligned box centred on (x, y) between two z levels."""
    return cq.Solid.makeBox(
        length_mm,
        width_mm,
        z_top - z_bottom,
        cq.Vector(x_mm - length_mm / 2, y_mm - width_mm / 2, z_bottom),
    )


def cylinder(
    diameter_mm: float, x_mm: float, y_mm: float, z_bottom: float, z_top: float
) -> cq.Shape:
    return cq.Solid.makeCylinder(diameter_mm / 2, z_top - z_bottom, cq.Vector(x_mm, y_mm, z_bottom))


def prism(
    outline: list[tuple[float, float]],
    x_mm: float,
    y_mm: float,
    z_bottom: float,
    z_top: float,
    offset_mm: float = 0.0,
) -> cq.Shape:
    """A closed polyline extruded between two z levels, optionally offset outwards."""
    pts = [(px + x_mm, py + y_mm) for px, py in outline]
    sketch = cq.Workplane("XY", origin=(0, 0, z_bottom)).polyline(pts).close()
    if offset_mm:
        sketch = sketch.offset2D(offset_mm, "intersection")
    solid = sketch.extrude(z_top - z_bottom).val()
    assert isinstance(solid, cq.Shape)
    return solid
