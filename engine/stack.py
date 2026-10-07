"""Tool specification and the closed-tool plate stack.

Kept free of CadQuery so the web UI and API can compute the stack and shut height
on servers without the geometry library.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


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
