"""Tool plates: die plate, stripper plate, punch holder plate and back plate.

Each plate is a rectangular block centred on (x, y). Thicknesses come from the
ToolSpec stack; holes and openings are cut later by the components they house.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from engine.features.base import BuildContext, Component, box


class PlateInput(BaseModel):
    length_mm: float = Field(gt=0, description="Along the strip feed (X)")
    width_mm: float = Field(gt=0, description="Across the strip (Y)")
    x_mm: float = 0.0
    y_mm: float = 0.0


def _plate(id: str, kind: str, p: PlateInput, z_bottom: float, z_top: float) -> Component:
    return Component(
        id=id,
        kind=kind,
        solid=box(p.length_mm, p.width_mm, p.x_mm, p.y_mm, z_bottom, z_top),
        metadata={
            "length_mm": p.length_mm,
            "width_mm": p.width_mm,
            "thickness_mm": z_top - z_bottom,
            "x_mm": p.x_mm,
            "y_mm": p.y_mm,
        },
    )


def die_plate(p: PlateInput, ctx: BuildContext) -> list[Component]:
    return [_plate("DP", "die_plate", p, ctx.stack.die_bottom, 0.0)]


def stripper_plate(p: PlateInput, ctx: BuildContext) -> list[Component]:
    s = ctx.stack
    return [_plate("SP", "stripper_plate", p, s.stripper_bottom, s.stripper_top)]


def punch_holder_plate(p: PlateInput, ctx: BuildContext) -> list[Component]:
    s = ctx.stack
    return [_plate("PH", "punch_holder_plate", p, s.holder_bottom, s.holder_top)]


def back_plate(p: PlateInput, ctx: BuildContext) -> list[Component]:
    s = ctx.stack
    return [_plate("BP", "back_plate", p, s.holder_top, s.back_top)]
