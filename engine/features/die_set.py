"""Die set: lower and upper shoes with four guide pillars and bushes, from the catalogue.

Pillars are pressed into the lower shoe; bushes are pressed into the upper shoe from
below, and the upper shoe has a through hole above each bush for the pillar.
"""

from __future__ import annotations

from pydantic import BaseModel

from engine.features.base import BuildContext, Component, Cut, box, cylinder
from rules.standards import Standards


class DieSetInput(BaseModel):
    """No parameters: the die set comes from ToolSpec.die_set_id and the catalogue."""


def die_set_row(standards: Standards, die_set_id: str) -> dict[str, float]:
    """The catalogue entry for a die set, all values in mm."""
    row = standards.lookup("die_sets", die_set_id=die_set_id)
    return {k: float(v) for k, v in row.items() if not isinstance(v, str)}


def die_set(params: DieSetInput, ctx: BuildContext) -> list[Component]:
    d = die_set_row(ctx.standards, ctx.spec.die_set_id)
    s = ctx.stack
    upper_bottom = s.back_top
    size = {"length_mm": d["length_mm"], "width_mm": d["width_mm"], "x_mm": 0.0, "y_mm": 0.0}
    lower = Component(
        id="LS",
        kind="lower_shoe",
        solid=box(d["length_mm"], d["width_mm"], 0, 0, s.lower_shoe_bottom, s.die_bottom),
        metadata=dict(size),
    )
    upper = Component(
        id="US",
        kind="upper_shoe",
        solid=box(d["length_mm"], d["width_mm"], 0, 0, upper_bottom, s.upper_shoe_top),
        metadata=dict(size),
    )
    components = [lower, upper]
    half_x, half_y = d["pillar_spacing_x_mm"] / 2, d["pillar_spacing_y_mm"] / 2
    corners = [(-half_x, -half_y), (half_x, -half_y), (-half_x, half_y), (half_x, half_y)]
    for i, (x, y) in enumerate(corners, start=1):
        pillar = cylinder(
            d["pillar_diameter_mm"],
            x,
            y,
            s.lower_shoe_bottom,
            s.lower_shoe_bottom + d["pillar_length_mm"],
        )
        bush_outer = cylinder(
            d["bush_outer_diameter_mm"], x, y, upper_bottom, upper_bottom + d["bush_length_mm"]
        )
        bore = cylinder(d["pillar_diameter_mm"], x, y, upper_bottom, s.upper_shoe_top)
        components.append(
            Component(
                id=f"G{i}",
                kind="guide_pillar",
                solid=pillar,
                metadata={"diameter_mm": d["pillar_diameter_mm"], "x_mm": x, "y_mm": y},
                cuts=[Cut("LS", pillar)],
            )
        )
        components.append(
            Component(
                id=f"GB{i}",
                kind="guide_bush",
                solid=bush_outer.cut(bore),
                metadata={"bore_mm": d["pillar_diameter_mm"], "x_mm": x, "y_mm": y},
                cuts=[Cut("US", bush_outer), Cut("US", bore)],
            )
        )
    return components
