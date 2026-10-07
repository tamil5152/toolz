"""Single-row strip layout: pitch, strip width and material utilisation.

The strip feeds along +X. For a part outline rotated by ``angle_deg``:

- pitch          = smallest feed step at which two neighbouring parts are at least
                   ``bridge_mm`` apart (true distance between outlines, so shapes that
                   nest into each other get a shorter pitch than their bounding box)
- strip width    = part height across the strip (Y extent) + 2 x edge allowance
- utilisation    = blank area / (pitch x strip width)

Bridge and edge allowance come from the strip_allowances standards table.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel

from rules.standards import Standards

ROTATION_STEP_DEG = 5.0
# Pitch search resolution, in mm.
PITCH_TOL_MM = 1e-3
_COARSE_STEPS = 200


class StripLayout(BaseModel):
    angle_deg: float
    pitch_mm: float
    strip_width_mm: float
    bridge_mm: float
    edge_allowance_mm: float
    blank_area_mm2: float
    utilisation: float  # 0..1


class StripLayoutOptions(BaseModel):
    at_0_deg: StripLayout
    at_180_deg: StripLayout
    best: StripLayout
    uses_placeholder: bool


def polygon_area_mm2(points: Sequence[tuple[float, float]]) -> float:
    pts = np.asarray(points, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def layout_at(
    outline: Sequence[tuple[float, float]],
    angle_deg: float,
    bridge_mm: float,
    edge_allowance_mm: float,
    blank_area_mm2: float | None = None,
) -> StripLayout:
    """Single-row layout of the part rotated by ``angle_deg``."""
    pts = _rotate(np.asarray(outline, dtype=float), angle_deg)
    area = polygon_area_mm2(outline) if blank_area_mm2 is None else blank_area_mm2
    pitch = _min_pitch(pts, bridge_mm)
    width = float(pts[:, 1].max() - pts[:, 1].min()) + 2 * edge_allowance_mm
    return StripLayout(
        angle_deg=angle_deg,
        pitch_mm=pitch,
        strip_width_mm=width,
        bridge_mm=bridge_mm,
        edge_allowance_mm=edge_allowance_mm,
        blank_area_mm2=area,
        utilisation=area / (pitch * width),
    )


def strip_layout_options(
    outline: Sequence[tuple[float, float]],
    thickness_mm: float,
    standards: Standards,
    blank_area_mm2: float | None = None,
) -> StripLayoutOptions:
    """Layouts at 0 and 180 degrees, and the best rotation in 5 degree steps."""
    row = standards.lookup("strip_allowances", thickness_mm=thickness_mm)
    bridge, edge = float(row["bridge_mm"]), float(row["edge_allowance_mm"])

    def at(angle: float) -> StripLayout:
        return layout_at(outline, angle, bridge, edge, blank_area_mm2)

    # A single row repeats every 180 degrees, so 0..175 covers every rotation.
    sweep = [at(i * ROTATION_STEP_DEG) for i in range(int(180 / ROTATION_STEP_DEG))]
    best = max(sweep, key=lambda layout: (round(layout.utilisation, 9), -layout.angle_deg))
    return StripLayoutOptions(
        at_0_deg=sweep[0],
        at_180_deg=at(180.0),
        best=best,
        uses_placeholder=standards.table("strip_allowances").placeholder,
    )


# --- geometry -------------------------------------------------------------------------


def _rotate(pts: np.ndarray, angle_deg: float) -> np.ndarray:
    a = math.radians(angle_deg)
    rotation = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return np.asarray(pts @ rotation.T)


def _min_pitch(pts: np.ndarray, bridge_mm: float) -> float:
    """Smallest feed step at which the part and its shifted copy are bridge_mm apart."""
    upper = float(pts[:, 0].max() - pts[:, 0].min()) + bridge_mm  # bounding box: always clear

    def clear(pitch: float) -> bool:
        return _polygon_distance(pts, pts + np.array([pitch, 0.0])) >= bridge_mm - PITCH_TOL_MM

    # Coarse scan first: clearance is not monotonic for parts that interlock.
    step = upper / _COARSE_STEPS
    low, high = 0.0, upper
    for i in range(1, _COARSE_STEPS + 1):
        if clear(i * step):
            low, high = (i - 1) * step, i * step
            break
    while high - low > PITCH_TOL_MM:
        mid = (low + high) / 2
        if clear(mid):
            high = mid
        else:
            low = mid
    return round(high, 3)


def _polygon_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Distance between two closed polygon outlines; 0 if they cross or touch."""
    a1, a2 = a, np.roll(a, -1, axis=0)
    b1, b2 = b, np.roll(b, -1, axis=0)
    if _any_segments_cross(a1, a2, b1, b2) or _contains(a, b[0]) or _contains(b, a[0]):
        return 0.0
    return min(_points_to_segments(a, b1, b2), _points_to_segments(b, a1, a2))


def _points_to_segments(p: np.ndarray, s1: np.ndarray, s2: np.ndarray) -> float:
    d = s2 - s1  # (m, 2)
    length_sq = np.maximum((d**2).sum(axis=1), 1e-12)
    rel = p[:, None, :] - s1[None, :, :]  # (n, m, 2)
    t = np.clip((rel * d[None]).sum(axis=2) / length_sq[None], 0.0, 1.0)
    nearest = s1[None] + t[..., None] * d[None]
    return float(np.sqrt(((p[:, None, :] - nearest) ** 2).sum(axis=2)).min())


def _any_segments_cross(a1: np.ndarray, a2: np.ndarray, b1: np.ndarray, b2: np.ndarray) -> bool:
    def orient(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> np.ndarray:
        return np.asarray(
            (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1])
            - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
        )

    A1, A2 = a1[:, None, :], a2[:, None, :]
    B1, B2 = b1[None, :, :], b2[None, :, :]
    o1, o2 = orient(A1, A2, B1), orient(A1, A2, B2)
    o3, o4 = orient(B1, B2, A1), orient(B1, B2, A2)
    return bool(np.any((o1 * o2 < 0) & (o3 * o4 < 0)))


def _contains(polygon: np.ndarray, point: np.ndarray) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = float(point[0]), float(point[1])
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside
