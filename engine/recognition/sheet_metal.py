"""Recognise the features of a flat sheet-metal part from a STEP file.

The part must be a single flat solid of constant thickness (a blank as it is cut
from the strip). The largest planar face is taken as the part's top face; its outer
wire is the outline and its inner wires are holes, slots and cut-outs. Concave
pockets in the outline are reported as notches.

Anything the recogniser cannot classify is listed in ``PartFeatures.unrecognised``
instead of being guessed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal

import cadquery as cq
import numpy as np
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.TopoDS import TopoDS
from pydantic import BaseModel, Field
from scipy.spatial import ConvexHull

from engine.export.step import import_step

# Geometric tolerance for comparing positions and sizes, in millimetres.
TOL_MM = 1e-3
# Angular step used to turn arcs into polyline points, in degrees.
ARC_STEP_DEG = 5.0


class Point2D(BaseModel):
    x_mm: float
    y_mm: float


class RoundHole(BaseModel):
    kind: Literal["round_hole"] = "round_hole"
    id: str
    diameter_mm: float
    centre: Point2D


class Slot(BaseModel):
    """An obround slot: two semicircular ends joined by straight sides."""

    kind: Literal["slot"] = "slot"
    id: str
    length_mm: float = Field(description="Overall length, end to end")
    width_mm: float
    centre: Point2D
    angle_deg: float = Field(description="Direction of the length axis, 0 to 180")


class RectCutout(BaseModel):
    kind: Literal["rect_cutout"] = "rect_cutout"
    id: str
    length_mm: float = Field(description="Longer side")
    width_mm: float = Field(description="Shorter side")
    corner_radius_mm: float
    centre: Point2D
    angle_deg: float = Field(description="Direction of the longer side, 0 to 180")


class Notch(BaseModel):
    """A concave pocket in the outline, measured against the outline's convex hull."""

    kind: Literal["notch"] = "notch"
    id: str
    opening_width_mm: float
    depth_mm: float
    opening_centre: Point2D


Feature = Annotated[RoundHole | Slot | RectCutout | Notch, Field(discriminator="kind")]


class PartFeatures(BaseModel):
    thickness_mm: float
    outline: list[Point2D] = Field(description="Closed polyline, first point not repeated")
    outline_perimeter_mm: float
    features: list[Feature]
    unrecognised: list[str] = Field(
        default_factory=list, description="Geometry the recogniser could not classify"
    )


class RecognitionError(ValueError):
    """The shape is not a flat sheet-metal part this recogniser can read."""


# --- public API -----------------------------------------------------------------------


def load_part(path: Path) -> PartFeatures:
    """Load a STEP file of a flat sheet-metal part and recognise its features."""
    return recognise(import_step(path))


def recognise(shape: cq.Shape) -> PartFeatures:
    """Recognise the features of a flat sheet-metal part given as a shape."""
    solids = shape.Solids()
    if len(solids) != 1:
        raise RecognitionError(f"expected exactly one solid, found {len(solids)}")
    solid = solids[0]

    top = _largest_planar_face(solid)
    normal = _unit(top.normalAt())
    thickness_mm = _thickness_mm(solid, top, normal)
    frame = _PlaneFrame(normal)

    unrecognised: list[str] = []
    expected_volume = top.Area() * thickness_mm
    if abs(solid.Volume() - expected_volume) > 0.01 * expected_volume:
        unrecognised.append(
            "part is not a flat plate of constant thickness "
            f"(volume {solid.Volume():.1f} mm3, flat plate would be {expected_volume:.1f} mm3)"
        )

    outline = _wire_polyline(top.outerWire(), top, frame)
    features: list[RoundHole | Slot | RectCutout | Notch] = []
    for wire in top.innerWires():
        feature = _classify_inner_wire(wire, top, frame)
        if isinstance(feature, str):
            unrecognised.append(feature)
        else:
            features.append(feature)
    features.extend(_notches(outline))

    return PartFeatures(
        thickness_mm=_round(thickness_mm),
        outline=[Point2D(x_mm=_round(x), y_mm=_round(y)) for x, y in outline],
        outline_perimeter_mm=_round(top.outerWire().Length()),
        features=_assign_ids(features),
        unrecognised=unrecognised,
    )


# --- faces and thickness --------------------------------------------------------------


def _largest_planar_face(solid: cq.Solid) -> cq.Face:
    planar = [f for f in solid.Faces() if f.geomType() == "PLANE"]
    if not planar:
        raise RecognitionError("part has no planar faces")
    largest = max(f.Area() for f in planar)
    candidates = [f for f in planar if f.Area() > largest * (1 - 1e-6)]
    # Top and bottom faces have equal area. Take the one facing +Z (then +Y, then +X)
    # so 2D coordinates match the part's own XY coordinates instead of being mirrored.
    return max(candidates, key=lambda f: tuple(np.round(_unit(f.normalAt())[::-1], 6)))


def _thickness_mm(solid: cq.Solid, top: cq.Face, normal: np.ndarray) -> float:
    top_centre = _vec(top.Center())
    distances = [
        abs(float(np.dot(top_centre - _vec(f.Center()), normal)))
        for f in solid.Faces()
        if f.geomType() == "PLANE" and abs(float(np.dot(_unit(f.normalAt()), normal))) > 1 - 1e-6
    ]
    distances = [d for d in distances if d > TOL_MM]
    if not distances:
        raise RecognitionError("no face parallel to the largest face; cannot find thickness")
    # The bottom face is the parallel face furthest from the top face.
    return max(distances)


class _PlaneFrame:
    """2D coordinates in the plane of the top face, aligned to global X where possible."""

    def __init__(self, normal: np.ndarray) -> None:
        x_axis = np.array([1.0, 0.0, 0.0])
        if abs(float(np.dot(x_axis, normal))) > 0.99:
            x_axis = np.array([0.0, 1.0, 0.0])
        x_axis = _unit_arr(x_axis - np.dot(x_axis, normal) * normal)
        self.x_axis = x_axis
        self.y_axis = np.cross(normal, x_axis)

    def to_2d(self, point: np.ndarray) -> tuple[float, float]:
        return float(np.dot(point, self.x_axis)), float(np.dot(point, self.y_axis))


# --- wires ----------------------------------------------------------------------------


def _ordered_edges(wire: cq.Wire, face: cq.Face) -> list[cq.Edge]:
    explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wire.wrapped), TopoDS.Face_s(face.wrapped))
    edges: list[cq.Edge] = []
    while explorer.More():
        edges.append(cq.Edge(explorer.Current()))
        explorer.Next()
    return edges


def _edge_points(edge: cq.Edge) -> list[np.ndarray]:
    """Points along an edge from one end to the other (end point included)."""
    if edge.geomType() == "LINE":
        count = 1
    elif edge.geomType() == "CIRCLE":
        sweep_deg = math.degrees(edge.Length() / edge.radius())
        count = max(2, math.ceil(sweep_deg / ARC_STEP_DEG))
    else:
        count = 32
    return [_vec(edge.positionAt(i / count)) for i in range(count + 1)]


def _wire_polyline(wire: cq.Wire, face: cq.Face, frame: _PlaneFrame) -> list[tuple[float, float]]:
    """The wire as a closed 2D polyline, in order, first point not repeated."""
    edges = [_edge_points(e) for e in _ordered_edges(wire, face)]
    if len(edges) > 1 and not _touches_either_end(edges[0][-1], edges[1]):
        edges[0].reverse()
    points: list[np.ndarray] = list(edges[0])
    for pts in edges[1:]:
        if np.linalg.norm(pts[0] - points[-1]) > np.linalg.norm(pts[-1] - points[-1]):
            pts = pts[::-1]
        points.extend(pts[1:])
    if np.linalg.norm(points[0] - points[-1]) < TOL_MM:
        points.pop()
    return [frame.to_2d(p) for p in points]


def _touches_either_end(point: np.ndarray, pts: list[np.ndarray]) -> bool:
    nearest = min(np.linalg.norm(point - pts[0]), np.linalg.norm(point - pts[-1]))
    return bool(nearest < TOL_MM)


# --- inner wires: holes, slots, cut-outs ----------------------------------------------


def _classify_inner_wire(
    wire: cq.Wire, face: cq.Face, frame: _PlaneFrame
) -> RoundHole | Slot | RectCutout | str:
    edges = _ordered_edges(wire, face)
    kinds = [e.geomType() for e in edges]
    lines = [e for e in edges if e.geomType() == "LINE"]
    arcs = [e for e in edges if e.geomType() == "CIRCLE"]
    centre_3d = _vec(wire.Center())
    where = "at ({:.2f}, {:.2f})".format(*frame.to_2d(centre_3d))

    if arcs and not lines and len(arcs) == len(edges) and _same_circle(arcs):
        cx, cy = frame.to_2d(_vec(arcs[0].arcCenter()))
        return RoundHole(
            id="",
            diameter_mm=_round(2 * arcs[0].radius()),
            centre=Point2D(x_mm=_round(cx), y_mm=_round(cy)),
        )

    if len(lines) == 2 and len(arcs) == 2 and len(edges) == 4:
        r1, r2 = arcs[0].radius(), arcs[1].radius()
        if abs(r1 - r2) < TOL_MM and _parallel(lines[0], lines[1]):
            c1 = np.array(frame.to_2d(_vec(arcs[0].arcCenter())))
            c2 = np.array(frame.to_2d(_vec(arcs[1].arcCenter())))
            mid = (c1 + c2) / 2
            return Slot(
                id="",
                length_mm=_round(float(np.linalg.norm(c2 - c1)) + 2 * r1),
                width_mm=_round(2 * r1),
                centre=Point2D(x_mm=_round(mid[0]), y_mm=_round(mid[1])),
                angle_deg=_round(_direction_deg(c2 - c1)),
            )

    if len(lines) == 4 and (len(arcs) == 0 or (len(arcs) == 4 and _same_radius(arcs))):
        rect = _rectangle(lines, frame)
        if rect is not None:
            length, width, angle = rect
            radius = arcs[0].radius() if arcs else 0.0
            cx, cy = frame.to_2d(centre_3d)
            return RectCutout(
                id="",
                length_mm=_round(length + (2 * radius if arcs else 0.0)),
                width_mm=_round(width + (2 * radius if arcs else 0.0)),
                corner_radius_mm=_round(radius),
                centre=Point2D(x_mm=_round(cx), y_mm=_round(cy)),
                angle_deg=_round(angle),
            )

    return f"inner contour {where} with edges {kinds} is not a round hole, slot or rectangle"


def _same_circle(arcs: list[cq.Edge]) -> bool:
    centre = _vec(arcs[0].arcCenter())
    return _same_radius(arcs) and all(
        np.linalg.norm(_vec(a.arcCenter()) - centre) < TOL_MM for a in arcs
    )


def _same_radius(arcs: list[cq.Edge]) -> bool:
    return all(abs(a.radius() - arcs[0].radius()) < TOL_MM for a in arcs)


def _parallel(a: cq.Edge, b: cq.Edge) -> bool:
    da = _unit_arr(_vec(a.endPoint()) - _vec(a.startPoint()))
    db = _unit_arr(_vec(b.endPoint()) - _vec(b.startPoint()))
    return abs(abs(float(np.dot(da, db))) - 1) < 1e-6


def _rectangle(lines: list[cq.Edge], frame: _PlaneFrame) -> tuple[float, float, float] | None:
    """(longer side, shorter side, direction of longer side) if the lines form a rectangle."""
    vectors = [
        np.array(frame.to_2d(_vec(e.endPoint()))) - np.array(frame.to_2d(_vec(e.startPoint())))
        for e in lines
    ]
    first = _unit_arr(vectors[0])
    along = [v for v in vectors if abs(abs(float(np.dot(_unit_arr(v), first))) - 1) < 1e-6]
    across = [v for v in vectors if abs(float(np.dot(_unit_arr(v), first))) < 1e-6]
    if len(along) != 2 or len(across) != 2:
        return None
    a = float(np.linalg.norm(along[0]))
    b = float(np.linalg.norm(across[0]))
    if abs(a - float(np.linalg.norm(along[1]))) > TOL_MM:
        return None
    if a >= b:
        return a, b, _direction_deg(along[0])
    return b, a, _direction_deg(across[0])


# --- notches --------------------------------------------------------------------------


def _notches(outline: list[tuple[float, float]]) -> list[Notch]:
    """Concave pockets: stretches of the outline that leave its convex hull.

    The opening of a notch runs between the last outline point on the hull line before
    the pocket and the first one after it; the depth is measured from that hull line.
    """
    pts = np.array(outline)
    hull = sorted(int(i) for i in ConvexHull(pts).vertices)
    n = len(pts)
    notches: list[Notch] = []
    for a, b in zip(hull, hull[1:] + [hull[0] + n], strict=True):
        line_start, line_end = pts[a % n], pts[b % n]
        depths = [_distance_to_line(pts[i % n], line_start, line_end) for i in range(a, b + 1)]
        i = 0
        while i < len(depths):
            if depths[i] < TOL_MM:
                i += 1
                continue
            first_off = i
            while depths[i] >= TOL_MM:
                i += 1
            start, end = pts[(a + first_off - 1) % n], pts[(a + i) % n]
            mid = (start + end) / 2
            notches.append(
                Notch(
                    id="",
                    opening_width_mm=_round(float(np.linalg.norm(end - start))),
                    depth_mm=_round(max(depths[first_off:i])),
                    opening_centre=Point2D(x_mm=_round(mid[0]), y_mm=_round(mid[1])),
                )
            )
    return notches


def _distance_to_line(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    return abs(float(ab[0] * (p - a)[1] - ab[1] * (p - a)[0])) / float(np.linalg.norm(ab))


# --- ids and helpers ------------------------------------------------------------------

_ID_PREFIX = {"round_hole": "H", "slot": "S", "rect_cutout": "R", "notch": "N"}


def _assign_ids(
    features: list[RoundHole | Slot | RectCutout | Notch],
) -> list[RoundHole | Slot | RectCutout | Notch]:
    """Ids like H1, S1, N1, numbered by position (bottom to top, then left to right)."""

    def position(f: RoundHole | Slot | RectCutout | Notch) -> tuple[float, float]:
        p = f.opening_centre if isinstance(f, Notch) else f.centre
        return round(p.y_mm, 2), round(p.x_mm, 2)

    counters: dict[str, int] = {}
    result: list[RoundHole | Slot | RectCutout | Notch] = []
    for f in sorted(features, key=lambda f: (f.kind, *position(f))):
        prefix = _ID_PREFIX[f.kind]
        counters[prefix] = counters.get(prefix, 0) + 1
        result.append(f.model_copy(update={"id": f"{prefix}{counters[prefix]}"}))
    return result


def _vec(v: cq.Vector) -> np.ndarray:
    return np.array([v.x, v.y, v.z])


def _unit(v: cq.Vector) -> np.ndarray:
    return _unit_arr(_vec(v))


def _unit_arr(v: np.ndarray) -> np.ndarray:
    return v / float(np.linalg.norm(v))


def _direction_deg(v: np.ndarray) -> float:
    angle = math.degrees(math.atan2(float(v[1]), float(v[0]))) % 180.0
    return 0.0 if angle > 180.0 - 1e-6 else angle


def _round(value: float) -> float:
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded
