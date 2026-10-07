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
_COARSE_STEPS = 60


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
        copy = pts + np.array([pitch, 0.0])
        return _copy_distance(pts, copy, bridge_mm) >= bridge_mm - PITCH_TOL_MM

    # No pitch below the longest horizontal chord can work, so the search starts there.
    start = min(_longest_chord(pts), upper)
    low, high = start, upper
    if not _is_convex(pts):
        # Clearance is not monotonic for parts that interlock: coarse scan first.
        step = (upper - start) / _COARSE_STEPS
        for i in range(_COARSE_STEPS + 1):
            if clear(start + i * step):
                low, high = start + max(i - 1, 0) * step, start + i * step
                break
    while high - low > PITCH_TOL_MM:
        mid = (low + high) / 2
        if clear(mid):
            high = mid
        else:
            low = mid
    return round(high, 3)


def _is_convex(pts: np.ndarray) -> bool:
    """True if every turn along the outline goes the same way (collinear points allowed).

    Two copies of a convex part only move apart as the pitch grows, so the pitch can
    be found by plain bisection.
    """
    edges = np.roll(pts, -1, axis=0) - pts
    turns = (
        edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1]
        - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
    )
    scale = float(np.abs(turns).max()) or 1.0
    turns = turns[np.abs(turns) > 1e-9 * scale]
    return bool(np.all(turns > 0) or np.all(turns < 0))


def _longest_chord(pts: np.ndarray) -> float:
    """Longest horizontal line inside the outline.

    A copy shifted by less than this along X overlaps that chord, so it is a lower
    bound for the pitch. Chord lengths vary linearly between vertex heights, so
    checking just above and below every vertex height finds the longest.
    """
    a, b = pts, np.roll(pts, -1, axis=0)
    longest = 0.0
    for y in np.unique(pts[:, 1]):
        for level in (y - 1e-6, y + 1e-6):
            crosses = (a[:, 1] > level) != (b[:, 1] > level)
            if not crosses.any():
                continue
            xa, ya, xb, yb = a[crosses, 0], a[crosses, 1], b[crosses, 0], b[crosses, 1]
            xs = np.sort(xa + (level - ya) * (xb - xa) / (yb - ya))
            longest = max(longest, float((xs[1::2] - xs[0::2]).max()))
    return longest


def _copy_distance(a: np.ndarray, b: np.ndarray, cutoff: float) -> float:
    """Distance between an outline ``a`` and its copy ``b`` shifted along +X; 0 if they cross.

    One copy cannot lie wholly inside the other (they have the same area), so crossing
    edges is the only way they overlap. Only distances below ``cutoff`` matter, so
    edges further apart than that along X are left out.
    """
    a1, a2 = a, np.roll(a, -1, axis=0)
    b1, b2 = b, np.roll(b, -1, axis=0)
    # Edges of a that reach towards b, and edges of b that reach back towards a.
    near_a = np.maximum(a1[:, 0], a2[:, 0]) >= b[:, 0].min() - cutoff
    near_b = np.minimum(b1[:, 0], b2[:, 0]) <= a[:, 0].max() + cutoff
    a1, a2, b1, b2 = a1[near_a], a2[near_a], b1[near_b], b2[near_b]
    if len(a1) == 0 or len(b1) == 0:
        return cutoff
    if _any_segments_cross(a1, a2, b1, b2):
        return 0.0
    return min(
        _points_to_segments(np.vstack([a1, a2]), b1, b2),
        _points_to_segments(np.vstack([b1, b2]), a1, a2),
    )


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
