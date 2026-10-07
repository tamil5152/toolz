"""Cutting punches, die buttons, die openings and pilots.

All are modelled in the closed position: punch tips at ``stack.punch_tip`` inside the
die, punch tops flush with the top of the punch holder.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from engine.features.base import BuildContext, Component, Cut, cylinder, prism


class PiercePunchInput(BaseModel):
    id: str = Field(description="Punch id, e.g. P1; its die button is P1-DB")
    x_mm: float
    y_mm: float
    diameter_mm: float = Field(gt=0)
    head_diameter_mm: float = Field(gt=0)
    head_thickness_mm: float = Field(gt=0)
    button_outer_diameter_mm: float = Field(gt=0)


def pierce_punch(p: PiercePunchInput, ctx: BuildContext) -> list[Component]:
    """Round pierce punch with a head, and its die button in the die plate."""
    s = ctx.stack
    head_bottom = s.holder_top - p.head_thickness_mm
    punch = cylinder(p.diameter_mm, p.x_mm, p.y_mm, s.punch_tip, head_bottom).fuse(
        cylinder(p.head_diameter_mm, p.x_mm, p.y_mm, head_bottom, s.holder_top)
    )
    clearance = ctx.die_clearance_per_side_mm
    bore_mm = p.diameter_mm + 2 * clearance
    button_outer = cylinder(p.button_outer_diameter_mm, p.x_mm, p.y_mm, s.die_bottom, 0.0)
    button = button_outer.cut(cylinder(bore_mm, p.x_mm, p.y_mm, s.die_bottom, 0.0))
    stripper_hole_mm = p.diameter_mm + 2 * ctx.spec.stripper_clearance_per_side_mm
    slug_hole_mm = bore_mm + 2 * ctx.spec.slug_relief_per_side_mm

    return [
        Component(
            id=p.id,
            kind="pierce_punch",
            solid=punch,
            metadata={
                "diameter_mm": p.diameter_mm,
                "x_mm": p.x_mm,
                "y_mm": p.y_mm,
                "die_id": f"{p.id}-DB",
                "cut_length_mm": math.pi * p.diameter_mm,
            },
            cuts=[
                Cut("PH", punch),
                Cut(
                    "SP",
                    cylinder(stripper_hole_mm, p.x_mm, p.y_mm, s.stripper_bottom, s.stripper_top),
                ),
            ],
        ),
        Component(
            id=f"{p.id}-DB",
            kind="die_button",
            solid=button,
            metadata={
                "bore_mm": bore_mm,
                "clearance_per_side_mm": clearance,
                "x_mm": p.x_mm,
                "y_mm": p.y_mm,
                "punch_id": p.id,
            },
            cuts=[
                Cut("DP", button_outer),
                Cut(
                    "LS", cylinder(slug_hole_mm, p.x_mm, p.y_mm, s.lower_shoe_bottom, s.die_bottom)
                ),
            ],
        ),
    ]


class BlankPunchInput(BaseModel):
    id: str = Field(description="Punch id, e.g. B1; its die opening is B1-DO")
    outline: list[tuple[float, float]] = Field(
        min_length=3, description="Blank outline in mm, closed, first point not repeated"
    )
    x_mm: float = Field(description="Station position: outline is shifted by (x, y)")
    y_mm: float


def blank_punch(p: BlankPunchInput, ctx: BuildContext) -> list[Component]:
    """Blanking punch shaped like the part outline, and its opening in the die plate.

    The die opening is the outline offset outwards by the die clearance; the punch
    is the outline itself (clearance on the die, as for blanking).
    """
    s = ctx.stack
    clearance = ctx.die_clearance_per_side_mm
    punch = prism(p.outline, p.x_mm, p.y_mm, s.punch_tip, s.holder_top)
    opening = prism(p.outline, p.x_mm, p.y_mm, s.die_bottom, 0.0, offset_mm=clearance)
    stripper_hole = prism(
        p.outline,
        p.x_mm,
        p.y_mm,
        s.stripper_bottom,
        s.stripper_top,
        offset_mm=ctx.spec.stripper_clearance_per_side_mm,
    )
    slug_hole = prism(
        p.outline,
        p.x_mm,
        p.y_mm,
        s.lower_shoe_bottom,
        s.die_bottom,
        offset_mm=clearance + ctx.spec.slug_relief_per_side_mm,
    )
    centre = punch.Center()
    return [
        Component(
            id=p.id,
            kind="blank_punch",
            solid=punch,
            metadata={
                "x_mm": centre.x,
                "y_mm": centre.y,
                "die_id": f"{p.id}-DO",
                "clearance_per_side_mm": clearance,
            },
            cuts=[Cut("PH", punch), Cut("SP", stripper_hole), Cut("LS", slug_hole)],
        ),
        # The die opening is not a part. It is kept as a virtual component holding the
        # opening's volume, so alignment checks can find it; clash checks skip it.
        Component(
            id=f"{p.id}-DO",
            kind="die_opening",
            solid=opening,
            metadata={"punch_id": p.id, "clearance_per_side_mm": clearance, "virtual": True},
            cuts=[Cut("DP", opening)],
        ),
    ]


class PilotInput(BaseModel):
    id: str
    x_mm: float
    y_mm: float
    diameter_mm: float = Field(gt=0)


def pilot(p: PilotInput, ctx: BuildContext) -> list[Component]:
    """Straight pilot from the holder top down to punch tip level, with a die relief hole."""
    s = ctx.stack
    solid = cylinder(p.diameter_mm, p.x_mm, p.y_mm, s.punch_tip, s.holder_top)
    relief_mm = p.diameter_mm + 2 * ctx.spec.slug_relief_per_side_mm
    stripper_mm = p.diameter_mm + 2 * ctx.spec.stripper_clearance_per_side_mm
    return [
        Component(
            id=p.id,
            kind="pilot",
            solid=solid,
            metadata={"diameter_mm": p.diameter_mm, "x_mm": p.x_mm, "y_mm": p.y_mm},
            cuts=[
                Cut("PH", solid),
                Cut("SP", cylinder(stripper_mm, p.x_mm, p.y_mm, s.stripper_bottom, s.stripper_top)),
                Cut("DP", cylinder(relief_mm, p.x_mm, p.y_mm, s.die_bottom, 0.0)),
            ],
        )
    ]
