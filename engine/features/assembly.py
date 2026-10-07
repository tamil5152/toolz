"""ToolAssembly: an ordered feature tree that regenerates the whole tool, and its checks.

The feature tree is plain JSON, for example::

    [
      {"feature": "tool_spec", "params": {"material": "mild_steel", ...}},
      {"feature": "die_set", "params": {}},
      {"feature": "die_plate", "params": {"length_mm": 160, "width_mm": 120}},
      {"feature": "pierce_punch", "params": {"id": "P1", "x_mm": 0, "y_mm": 0, ...}}
    ]

``build`` runs every feature function in order, then cuts each component's holes
and openings into the plates that house it. ``BuiltAssembly.check`` runs the
geometric checks and the YAML rules and returns one list of results.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cadquery as cq
from pydantic import BaseModel

from engine.features.base import BuildContext, Component, Stack, ToolSpec
from engine.features.die_set import DieSetInput, die_set, die_set_row
from engine.features.fasteners import DowelInput, ScrewInput, dowels, screws
from engine.features.plates import (
    PlateInput,
    back_plate,
    die_plate,
    punch_holder_plate,
    stripper_plate,
)
from engine.features.punches import (
    BlankPunchInput,
    PiercePunchInput,
    PilotInput,
    blank_punch,
    pierce_punch,
    pilot,
)
from engine.validity import is_valid_solid
from rules.engine import RuleResult, RulesEngine, Status, Subject
from rules.standards import Standards

FeatureFunction = Callable[[Any, BuildContext], list[Component]]

FEATURES: dict[str, tuple[type[BaseModel], FeatureFunction]] = {
    "die_set": (DieSetInput, die_set),
    "die_plate": (PlateInput, die_plate),
    "stripper_plate": (PlateInput, stripper_plate),
    "punch_holder_plate": (PlateInput, punch_holder_plate),
    "back_plate": (PlateInput, back_plate),
    "pierce_punch": (PiercePunchInput, pierce_punch),
    "blank_punch": (BlankPunchInput, blank_punch),
    "pilot": (PilotInput, pilot),
    "screws": (ScrewInput, screws),
    "dowels": (DowelInput, dowels),
}

# Overlap below this volume is numerical noise from coincident faces, not a clash.
CLASH_VOLUME_TOL_MM3 = 0.01
# Punch-to-die gap may differ from the specified clearance by this much.
ALIGNMENT_TOL_MM = 0.005


class FeatureCall(BaseModel):
    feature: str
    params: dict[str, Any]


class ToolAssembly:
    """An ordered feature tree. The first feature is always the tool_spec."""

    def __init__(self, spec: ToolSpec) -> None:
        self.features: list[FeatureCall] = [
            FeatureCall(feature="tool_spec", params=spec.model_dump())
        ]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec.model_validate(self.features[0].params)

    def add(self, feature: str, params: dict[str, Any] | BaseModel) -> FeatureCall:
        """Append a feature after validating its parameters."""
        if feature not in FEATURES:
            raise ValueError(f"unknown feature {feature!r}; known: {sorted(FEATURES)}")
        model, _ = FEATURES[feature]
        raw = params.model_dump() if isinstance(params, BaseModel) else params
        call = FeatureCall(feature=feature, params=model.model_validate(raw).model_dump())
        self.features.append(call)
        return call

    def to_json(self) -> str:
        return json.dumps([f.model_dump() for f in self.features], indent=2)

    @classmethod
    def from_json(cls, text: str) -> ToolAssembly:
        calls = [FeatureCall.model_validate(item) for item in json.loads(text)]
        if not calls or calls[0].feature != "tool_spec":
            raise ValueError("the first feature must be tool_spec")
        assembly = cls(ToolSpec.model_validate(calls[0].params))
        for call in calls[1:]:
            assembly.add(call.feature, call.params)
        return assembly

    def build(self, standards: Standards) -> BuiltAssembly:
        spec = self.spec
        shoes = die_set_row(standards, spec.die_set_id)
        stack = Stack.from_spec(
            spec, shoes["lower_shoe_thickness_mm"], shoes["upper_shoe_thickness_mm"]
        )
        ctx = BuildContext(spec=spec, stack=stack, standards=standards)
        built = BuiltAssembly(spec=spec, stack=stack, ctx=ctx)

        for call in self.features[1:]:
            model, function = FEATURES[call.feature]
            for component in function(model.model_validate(call.params), ctx):
                if component.id in built.components:
                    built.problems.append(
                        _geo("GEO-ID", component.id, f"duplicate component id {component.id}")
                    )
                    continue
                built.components[component.id] = component

        for component in list(built.components.values()):
            for cut in component.cuts:
                target = built.components.get(cut.target_id)
                if target is None:
                    built.problems.append(
                        _geo(
                            "GEO-CUT",
                            component.id,
                            f"{component.id} needs a hole in {cut.target_id}, "
                            "which is not in the tool",
                        )
                    )
                    continue
                target.solid = target.solid.cut(cut.solid)
        return built


@dataclass
class BuiltAssembly:
    spec: ToolSpec
    stack: Stack
    ctx: BuildContext
    components: dict[str, Component] = field(default_factory=dict)
    problems: list[RuleResult] = field(default_factory=list)

    @property
    def parts(self) -> list[Component]:
        """Real parts: everything except virtual components such as die openings."""
        return [c for c in self.components.values() if not c.metadata.get("virtual")]

    def check(self, rules: RulesEngine) -> list[RuleResult]:
        """All geometric checks and YAML rules. Any 'fail' blocks release."""
        return [
            *self.problems,
            *self.check_validity(),
            *self.check_interference(),
            *self.check_punch_die_alignment(),
            *rules.check(self.rule_subjects()),
        ]

    def check_validity(self) -> list[RuleResult]:
        return [
            _geo("GEO-VALID", c.id, f"{c.id} is not a valid solid")
            for c in self.parts
            if not is_valid_solid(c.solid)
        ]

    def check_interference(self) -> list[RuleResult]:
        parts = self.parts
        boxes = {c.id: c.solid.BoundingBox() for c in parts}
        results = []
        for i, a in enumerate(parts):
            for b in parts[i + 1 :]:
                if not _boxes_overlap(boxes[a.id], boxes[b.id]):
                    continue
                volume = a.solid.intersect(b.solid).Volume()
                if volume > CLASH_VOLUME_TOL_MM3:
                    results.append(
                        _geo(
                            "GEO-CLASH",
                            f"{a.id}/{b.id}",
                            f"{a.id} and {b.id} overlap by {volume:.3f} mm3",
                            {"overlap_mm3": round(volume, 3)},
                        )
                    )
        return results

    def check_punch_die_alignment(self) -> list[RuleResult]:
        """The gap between each punch and its die must equal the die clearance."""
        results = []
        for punch in self.components.values():
            die_id = punch.metadata.get("die_id")
            if die_id is None:
                continue
            die = self.components[die_id]
            expected = float(die.metadata["clearance_per_side_mm"])
            # A die opening is a virtual volume; the real die is the die plate around it.
            if die.metadata.get("virtual"):
                if "DP" not in self.components:
                    continue  # already reported as a missing plate (GEO-CUT)
                die_solid = self.components["DP"].solid
            else:
                die_solid = die.solid
            gap = punch.solid.distance(die_solid)
            measured = {"gap_mm": round(gap, 4), "clearance_per_side_mm": round(expected, 4)}
            if abs(gap - expected) > ALIGNMENT_TOL_MM:
                results.append(
                    _geo(
                        "GEO-ALIGN",
                        punch.id,
                        f"{punch.id} to {die_id}: gap {gap:.4f} mm, "
                        f"clearance should be {expected:.4f} mm per side",
                        measured,
                    )
                )
            else:
                results.append(
                    _geo(
                        "GEO-ALIGN", punch.id, f"{punch.id} aligned with {die_id}", measured, "pass"
                    )
                )
        return results

    def rule_subjects(self) -> list[Subject]:
        """Values for the YAML rules: the tool, each punch, each screw and dowel."""
        spec = self.spec
        subjects = [
            Subject(
                kind="tool",
                id="TOOL",
                values={"press_id": spec.press_id, "shut_height_mm": self.stack.shut_height_mm},
            )
        ]
        for c in self.components.values():
            if c.kind in ("pierce_punch", "blank_punch"):
                values: dict[str, str | float] = {
                    "material": spec.material,
                    "thickness_mm": spec.sheet_thickness_mm,
                }
                if "diameter_mm" in c.metadata:
                    values["diameter_mm"] = float(c.metadata["diameter_mm"])
                subjects.append(Subject(kind=c.kind, id=c.id, values=values))
            elif c.kind in ("screw", "dowel"):
                subjects.append(
                    Subject(
                        kind=c.kind,
                        id=c.id,
                        values={
                            "fastener": c.kind,
                            "diameter_mm": float(c.metadata["diameter_mm"]),
                            "edge_distance_mm": self._edge_distance_mm(c),
                        },
                    )
                )
        return subjects

    def _edge_distance_mm(self, fastener: Component) -> float:
        x, y = float(fastener.metadata["x_mm"]), float(fastener.metadata["y_mm"])
        distances = []
        for plate_id in fastener.metadata["plates"]:
            plate = self.components.get(plate_id)
            if plate is None:
                continue
            m = plate.metadata
            half_l, half_w = float(m["length_mm"]) / 2, float(m["width_mm"]) / 2
            dx = half_l - abs(x - float(m["x_mm"]))
            dy = half_w - abs(y - float(m["y_mm"]))
            distances.append(min(dx, dy))
        return min(distances) if distances else 0.0

    def to_step(self, path: Path) -> Path:
        assembly = cq.Assembly(name="tool")
        for c in self.parts:
            assembly.add(c.solid, name=c.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        assembly.export(str(path), "STEP")
        return path


def _boxes_overlap(a: cq.BoundBox, b: cq.BoundBox) -> bool:
    return (
        a.xmin < b.xmax
        and b.xmin < a.xmax
        and a.ymin < b.ymax
        and b.ymin < a.ymax
        and a.zmin < b.zmax
        and b.zmin < a.zmax
    )


def _geo(
    rule_id: str,
    subject_id: str,
    message: str,
    measured: dict[str, Any] | None = None,
    status: Status = "fail",
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        subject_id=subject_id,
        status=status,
        measured=measured or {},
        message=message,
        source="Geometry check (OpenCASCADE)",
    )
