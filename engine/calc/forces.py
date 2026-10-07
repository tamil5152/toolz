"""Cutting force, stripping force, press tonnage and centre of pressure.

Formulas (all forces in newtons unless the name says kN):

- cutting force        F_cut   = L x t x tau          (L cut length mm, t thickness mm,
                                                       tau shear strength N/mm2)
- stripping force      F_strip = F_cut x stripping_pct / 100
- press force          F_press = (sum F_cut + sum F_strip) x safety factor
- centre of pressure   x_c = sum(L_i x_i) / sum(L_i), y_c likewise, where (x_i, y_i)
                       is the centroid of cut contour i taken as a line, not an area.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, Field

from rules.standards import Standards

NEWTONS_PER_TONNE_FORCE = 9806.65


class CuttingContour(BaseModel):
    """One closed contour cut in one press stroke (a hole, a blank outline)."""

    id: str
    length_mm: float = Field(gt=0)
    centroid_x_mm: float
    centroid_y_mm: float


class PressForces(BaseModel):
    cutting_force_n: float
    stripping_force_n: float
    safety_factor: float
    press_force_kn: float
    press_force_tonnes: float = Field(description="Tonnes-force, for comparison with press ratings")
    centre_of_pressure_x_mm: float
    centre_of_pressure_y_mm: float
    uses_placeholder: bool


def cutting_force_n(
    cut_length_mm: float, thickness_mm: float, shear_strength_n_per_mm2: float
) -> float:
    return cut_length_mm * thickness_mm * shear_strength_n_per_mm2


def stripping_force_n(cutting_force: float, stripping_force_pct: float) -> float:
    return cutting_force * stripping_force_pct / 100.0


def press_force_kn(total_cutting_n: float, total_stripping_n: float, safety_factor: float) -> float:
    return (total_cutting_n + total_stripping_n) * safety_factor / 1000.0


def centre_of_pressure_mm(contours: Sequence[CuttingContour]) -> tuple[float, float]:
    total = sum(c.length_mm for c in contours)
    if total <= 0:
        raise ValueError("no cutting contours")
    x = sum(c.length_mm * c.centroid_x_mm for c in contours) / total
    y = sum(c.length_mm * c.centroid_y_mm for c in contours) / total
    return x, y


def polyline_contour(id: str, points: Sequence[tuple[float, float]]) -> CuttingContour:
    """A closed polyline (first point not repeated) as a contour with its line centroid."""
    pts = np.asarray(points, dtype=float)
    seg_start, seg_end = pts, np.roll(pts, -1, axis=0)
    lengths = np.linalg.norm(seg_end - seg_start, axis=1)
    mids = (seg_start + seg_end) / 2
    total = float(lengths.sum())
    centroid = (mids * lengths[:, None]).sum(axis=0) / total
    return CuttingContour(
        id=id, length_mm=total, centroid_x_mm=float(centroid[0]), centroid_y_mm=float(centroid[1])
    )


def circle_contour(id: str, diameter_mm: float, x_mm: float, y_mm: float) -> CuttingContour:
    return CuttingContour(
        id=id, length_mm=float(np.pi * diameter_mm), centroid_x_mm=x_mm, centroid_y_mm=y_mm
    )


def press_forces(
    contours: Sequence[CuttingContour],
    thickness_mm: float,
    material: str,
    standards: Standards,
) -> PressForces:
    """Forces for cutting all ``contours`` in one stroke, with values from standards."""
    shear = standards.value("materials", "shear_strength_n_per_mm2", material=material)
    stripping_pct = standards.value("press_factors", "stripping_force_pct", name="default")
    safety = standards.value("press_factors", "press_safety_factor", name="default")

    cutting = sum(cutting_force_n(c.length_mm, thickness_mm, shear) for c in contours)
    stripping = stripping_force_n(cutting, stripping_pct)
    press_kn = press_force_kn(cutting, stripping, safety)
    cx, cy = centre_of_pressure_mm(contours)
    return PressForces(
        cutting_force_n=cutting,
        stripping_force_n=stripping,
        safety_factor=safety,
        press_force_kn=press_kn,
        press_force_tonnes=press_kn * 1000.0 / NEWTONS_PER_TONNE_FORCE,
        centre_of_pressure_x_mm=cx,
        centre_of_pressure_y_mm=cy,
        uses_placeholder=bool({"materials", "press_factors"} & set(standards.placeholder_tables)),
    )
