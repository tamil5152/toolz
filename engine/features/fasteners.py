"""Socket head screws and dowels, sized from the standards tables.

Lower fasteners hold the die plate to the lower shoe: screws go in from below
(head in a counterbore in the lower shoe), dowels pass through both plates.
Upper fasteners hold the punch holder and back plate to the upper shoe: screws go in
from above, dowels pass through all three.

Each fastener cuts its own exact shape from the plates it passes through, so a
fastener never clashes with its own holes.
"""

from __future__ import annotations

from typing import Literal

import cadquery as cq
from pydantic import BaseModel, Field

from engine.features.base import BuildContext, Component, Cut, Point, cylinder

Side = Literal["lower", "upper"]

LOWER_PLATES: tuple[str, ...] = ("LS", "DP")
UPPER_PLATES: tuple[str, ...] = ("US", "BP", "PH")


class ScrewInput(BaseModel):
    id: str = Field(description="Group id, e.g. SC1; screws are SC1-1, SC1-2, ...")
    size: str = Field(description="Size in the screws table, e.g. M8")
    side: Side
    positions: list[Point] = Field(min_length=1)


class DowelInput(BaseModel):
    id: str = Field(description="Group id, e.g. DW1; dowels are DW1-1, DW1-2, ...")
    diameter_mm: float = Field(gt=0, description="Must be a size in the dowels table")
    side: Side
    positions: list[Point] = Field(min_length=1)


def screws(p: ScrewInput, ctx: BuildContext) -> list[Component]:
    row = ctx.standards.lookup("screws", size=p.size)
    d = float(row["diameter_mm"])
    head_d = float(row["head_diameter_mm"])
    head_h = float(row["head_height_mm"])
    engagement = float(row["thread_engagement_mm"])
    s = ctx.stack

    components = []
    for i, pos in enumerate(p.positions, start=1):
        x, y = pos.x_mm, pos.y_mm
        solid: cq.Shape
        if p.side == "lower":
            head_top = s.lower_shoe_bottom + head_h
            solid = cylinder(head_d, x, y, s.lower_shoe_bottom, head_top).fuse(
                cylinder(d, x, y, head_top, s.die_bottom + engagement)
            )
            plates = LOWER_PLATES
        else:
            head_bottom = s.upper_shoe_top - head_h
            solid = cylinder(head_d, x, y, head_bottom, s.upper_shoe_top).fuse(
                cylinder(d, x, y, s.holder_top - engagement, head_bottom)
            )
            plates = UPPER_PLATES
        components.append(
            Component(
                id=f"{p.id}-{i}",
                kind="screw",
                solid=solid,
                metadata={"size": p.size, "diameter_mm": d, "x_mm": x, "y_mm": y, "plates": plates},
                cuts=[Cut(plate, solid) for plate in plates],
            )
        )
    return components


def dowels(p: DowelInput, ctx: BuildContext) -> list[Component]:
    ctx.standards.lookup("dowels", diameter_mm=p.diameter_mm)  # size must be standard
    s = ctx.stack
    if p.side == "lower":
        z_bottom, z_top, plates = s.lower_shoe_bottom, 0.0, LOWER_PLATES
    else:
        z_bottom, z_top, plates = s.holder_bottom, s.upper_shoe_top, UPPER_PLATES

    components = []
    for i, pos in enumerate(p.positions, start=1):
        solid = cylinder(p.diameter_mm, pos.x_mm, pos.y_mm, z_bottom, z_top)
        components.append(
            Component(
                id=f"{p.id}-{i}",
                kind="dowel",
                solid=solid,
                metadata={
                    "diameter_mm": p.diameter_mm,
                    "x_mm": pos.x_mm,
                    "y_mm": pos.y_mm,
                    "plates": plates,
                },
                cuts=[Cut(plate, solid) for plate in plates],
            )
        )
    return components
