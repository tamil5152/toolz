"""SVG drawings for the web UI, generated in Python: blank, strip layout and tool stack.

Colours come from CSS classes in the page, so drawings follow the light or dark theme.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

from engine.calc.strip_layout import StripLayout
from engine.stack import Stack
from server.designer import DesignInput, Hole

Point = tuple[float, float]


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _poly(points: Sequence[Point], cls: str) -> str:
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polygon class="{cls}" points="{pts}"/>'


def _text(x: float, y: float, label: str, cls: str = "lbl", anchor: str = "middle") -> str:
    return (
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{escape(label)}</text>'
    )


def _svg(width: float, height: float, body: str, title: str) -> str:
    return (
        f'<svg class="drawing" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        f'aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{escape(title)}</title>{body}</svg>"
    )


def _rotate(points: Sequence[Point], angle_deg: float) -> list[Point]:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [(x * c - y * s, x * s + y * c) for x, y in points]


# --- blank ----------------------------------------------------------------------------


def blank_svg(outline: Sequence[Point], holes: Sequence[Hole]) -> str:
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    size = 340.0
    scale = (size - 80) / max(w, h, 1e-9)
    ox, oy = 50 - min(xs) * scale, 30 + max(ys) * scale

    def tx(p: Point) -> Point:
        return ox + p[0] * scale, oy - p[1] * scale

    body = [_poly([tx(p) for p in outline], "part")]
    for i, hole in enumerate(holes, start=1):
        cx, cy = tx((hole.x_mm, hole.y_mm))
        body.append(
            f'<circle class="hole" cx="{cx:.2f}" cy="{cy:.2f}" r="{hole.diameter_mm / 2 * scale:.2f}"/>'
        )
        body.append(
            _text(
                cx,
                cy - hole.diameter_mm / 2 * scale - 4,
                f"H{i} Ø{_fmt(hole.diameter_mm)}",
                "lbl small",
            )
        )
    bottom = oy + 22
    body.append(
        f'<line class="dim" x1="{ox + min(xs) * scale:.1f}" y1="{bottom - 8:.1f}" x2="{ox + max(xs) * scale:.1f}" y2="{bottom - 8:.1f}"/>'
    )
    body.append(_text(ox + (min(xs) + max(xs)) / 2 * scale, bottom + 6, f"{_fmt(w)} mm"))
    left = ox + min(xs) * scale - 14
    body.append(
        f'<line class="dim" x1="{left:.1f}" y1="{oy - min(ys) * scale:.1f}" x2="{left:.1f}" y2="{oy - max(ys) * scale:.1f}"/>'
    )
    body.append(
        f'<text class="lbl" x="{left - 6:.1f}" y="{oy - (min(ys) + max(ys)) / 2 * scale:.1f}" '
        f'text-anchor="middle" transform="rotate(-90 {left - 6:.1f} {oy - (min(ys) + max(ys)) / 2 * scale:.1f})">'
        f"{_fmt(h)} mm</text>"
    )
    return _svg(size, oy - min(ys) * scale + 50, "".join(body), "Blank with holes")


# --- strip layout ---------------------------------------------------------------------


def strip_svg(
    outline: Sequence[Point], holes: Sequence[Hole], layout: StripLayout, parts: int = 3
) -> str:
    rotated = _rotate(outline, layout.angle_deg)
    min_x = min(p[0] for p in rotated)
    min_y = min(p[1] for p in rotated)
    # Part placed so its lowest point sits one edge allowance above the strip edge.
    shift = (-min_x + layout.bridge_mm, -min_y + layout.edge_allowance_mm)
    strip_length = layout.pitch_mm * parts + layout.bridge_mm
    width_px = 640.0
    scale = (width_px - 40) / strip_length
    height = layout.strip_width_mm * scale + 70
    ox, oy = 20.0, 20 + layout.strip_width_mm * scale

    def tx(p: Point) -> Point:
        return ox + p[0] * scale, oy - p[1] * scale

    body = [
        f'<rect class="strip" x="{ox:.1f}" y="{oy - layout.strip_width_mm * scale:.1f}" '
        f'width="{strip_length * scale:.1f}" height="{layout.strip_width_mm * scale:.1f}"/>'
    ]
    rot_holes = [(h, _rotate([(h.x_mm, h.y_mm)], layout.angle_deg)[0]) for h in holes]
    for n in range(parts):
        dx = shift[0] + n * layout.pitch_mm
        body.append(_poly([tx((x + dx, y + shift[1])) for x, y in rotated], "part"))
        for hole, (hx, hy) in rot_holes:
            cx, cy = tx((hx + dx, hy + shift[1]))
            body.append(
                f'<circle class="hole" cx="{cx:.2f}" cy="{cy:.2f}" r="{hole.diameter_mm / 2 * scale:.2f}"/>'
            )
    # Pitch dimension between the first two parts.
    x1 = ox + (shift[0] + min_x) * scale
    y_dim = oy + 18
    body.append(
        f'<line class="dim" x1="{x1:.1f}" y1="{y_dim:.1f}" x2="{x1 + layout.pitch_mm * scale:.1f}" y2="{y_dim:.1f}"/>'
    )
    body.append(
        _text(x1 + layout.pitch_mm * scale / 2, y_dim + 16, f"pitch {_fmt(layout.pitch_mm)} mm")
    )
    body.append(
        _text(
            ox + strip_length * scale - 4,
            oy + 34,
            f"strip width {_fmt(layout.strip_width_mm)} mm",
            "lbl",
            "end",
        )
    )
    body.append(_text(ox + 4, oy + 34, "feed →", "lbl", "start"))
    return _svg(
        width_px, height + 20, "".join(body), f"Strip layout at {_fmt(layout.angle_deg)} degrees"
    )


# --- tool stack -----------------------------------------------------------------------

_LAYERS = [
    # (label, css class, bottom attr, top attr, width source)
    ("Upper shoe", "shoe", "back_top", "upper_shoe_top", "die_set"),
    ("Back plate", "plate", "holder_top", "back_top", "plate"),
    ("Punch holder", "plate", "holder_bottom", "holder_top", "plate"),
    ("Stripper", "stripper", "stripper_bottom", "stripper_top", "plate"),
    ("Die plate", "die", "die_bottom", "zero", "plate"),
    ("Lower shoe", "shoe", "lower_shoe_bottom", "die_bottom", "die_set"),
]


def _z(stack: Stack, name: str) -> float:
    return 0.0 if name == "zero" else float(getattr(stack, name))


def stack_elevation_svg(stack: Stack, inputs: DesignInput, die_set: dict[str, float]) -> str:
    """Front view of the closed tool, looking along the strip feed."""
    set_length = die_set.get("length_mm", inputs.plate_length_mm)
    width_px, scale = 420.0, 1.0
    scale = min((width_px - 150) / max(set_length, 1), 420 / max(stack.shut_height_mm, 1))
    cx = 20 + set_length * scale / 2
    top = 20 + stack.upper_shoe_top * scale

    def zy(z: float) -> float:
        return top - z * scale

    body = []
    for label, cls, bottom, top_attr, source in _LAYERS:
        length = set_length if source == "die_set" else inputs.plate_length_mm
        z0, z1 = _z(stack, bottom), _z(stack, top_attr)
        body.append(
            f'<rect class="{cls}" x="{cx - length * scale / 2:.1f}" y="{zy(z1):.1f}" '
            f'width="{length * scale:.1f}" height="{max((z1 - z0) * scale, 0.5):.1f}"/>'
        )
        body.append(
            _text(
                cx + set_length * scale / 2 + 8,
                (zy(z0) + zy(z1)) / 2 + 4,
                f"{label} {_fmt(z1 - z0)}",
                "lbl small",
                "start",
            )
        )
    # Punch and strip.
    punch_w = max(inputs.plate_length_mm * 0.08, 4)
    body.append(
        f'<rect class="punch" x="{cx - punch_w * scale / 2:.1f}" y="{zy(stack.holder_top):.1f}" '
        f'width="{punch_w * scale:.1f}" height="{(stack.holder_top - stack.punch_tip) * scale:.1f}"/>'
    )
    body.append(
        f'<rect class="sheet" x="{cx - inputs.plate_length_mm * scale / 2 - 10:.1f}" y="{zy(stack.sheet_thickness):.1f}" '
        f'width="{inputs.plate_length_mm * scale + 20:.1f}" height="{max(stack.sheet_thickness * scale, 1.5):.1f}"/>'
    )
    # Shut height dimension.
    x_dim = 10.0
    body.append(
        f'<line class="dim" x1="{x_dim}" y1="{zy(stack.upper_shoe_top):.1f}" x2="{x_dim}" y2="{zy(stack.lower_shoe_bottom):.1f}"/>'
    )
    mid = (zy(stack.upper_shoe_top) + zy(stack.lower_shoe_bottom)) / 2
    body.append(
        f'<text class="lbl" x="{x_dim + 4}" y="{mid:.1f}" transform="rotate(-90 {x_dim + 4} {mid:.1f})" '
        f'text-anchor="middle" dy="10">shut height {_fmt(stack.shut_height_mm)} mm</text>'
    )
    return _svg(
        width_px, zy(stack.lower_shoe_bottom) + 20, "".join(body), "Tool stack, closed position"
    )


def stack_iso_svg(stack: Stack, inputs: DesignInput, die_set: dict[str, float]) -> str:
    """Isometric view of the plates and shoes of the closed tool, exploded slightly."""
    set_l = die_set.get("length_mm", inputs.plate_length_mm)
    set_w = die_set.get("width_mm", inputs.plate_width_mm)
    # Exploded spacing between layers so each one shows; a drawing aid, not a tool value.
    gap = max(set_l, set_w) * 0.1
    cos30, sin30 = math.cos(math.radians(30)), 0.5
    layers = list(reversed(_LAYERS))  # bottom first, so upper layers are drawn on top

    boxes = []
    offset = 0.0
    for i, (label, cls, bottom, top_attr, source) in enumerate(layers):
        length, width = (
            (set_l, set_w)
            if source == "die_set"
            else (inputs.plate_length_mm, inputs.plate_width_mm)
        )
        z0, z1 = _z(stack, bottom), _z(stack, top_attr)
        boxes.append((label, cls, length, width, z0 + offset, z1 + offset))
        offset += gap if i < len(layers) - 1 else 0
    extent = max(set_l, set_w)
    total_h = boxes[-1][5] - boxes[0][4]
    scale = 300 / (extent * cos30 * 2 + total_h)

    def iso(x: float, y: float, z: float) -> Point:
        return (x - y) * cos30 * scale, ((x + y) * sin30 - z) * scale

    faces: list[tuple[str, list[Point]]] = []
    for _label, cls, length, width, z0, z1 in boxes:
        hx, hy = length / 2, width / 2
        side = [iso(hx, -hy, z0), iso(hx, hy, z0), iso(hx, hy, z1), iso(hx, -hy, z1)]
        front = [iso(-hx, hy, z0), iso(hx, hy, z0), iso(hx, hy, z1), iso(-hx, hy, z1)]
        top = [iso(-hx, -hy, z1), iso(hx, -hy, z1), iso(hx, hy, z1), iso(-hx, hy, z1)]
        faces += [(f"{cls} iso-side", side), (f"{cls} iso-front", front), (f"{cls} iso-top", top)]
    all_pts = [p for _, pts in faces for p in pts]
    min_x, min_y = min(p[0] for p in all_pts), min(p[1] for p in all_pts)
    max_x, max_y = max(p[0] for p in all_pts), max(p[1] for p in all_pts)
    pad = 12.0
    body = [_poly([(x - min_x + pad, y - min_y + pad) for x, y in pts], cls) for cls, pts in faces]
    return _svg(
        max_x - min_x + 2 * pad,
        max_y - min_y + 2 * pad,
        "".join(body),
        "Isometric view of the tool stack",
    )
