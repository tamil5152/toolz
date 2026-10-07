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
from pydantic import BaseModel

from engine.stack import Stack, ToolSpec
from rules.standards import Standards

__all__ = [
    "BuildContext",
    "Component",
    "Cut",
    "Point",
    "Stack",
    "ToolSpec",
    "box",
    "cylinder",
    "prism",
]


class Point(BaseModel):
    x_mm: float
    y_mm: float


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
