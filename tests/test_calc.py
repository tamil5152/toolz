"""Press tool calculations checked against hand calculations.

The standards used here are written in the tests, so the expected numbers do not
change when the shop's own tables replace the placeholders in standards/.
"""

import math

import pytest

from engine.calc.forces import (
    CuttingContour,
    centre_of_pressure_mm,
    circle_contour,
    cutting_force_n,
    polyline_contour,
    press_force_kn,
    press_forces,
    stripping_force_n,
)
from engine.calc.strip_layout import layout_at, polygon_area_mm2, strip_layout_options
from rules.standards import Standards, Table

STANDARDS = Standards(
    {
        "materials": Table(
            table="materials",
            source="test",
            rows=[{"material": "mild_steel", "shear_strength_n_per_mm2": 300}],
        ),
        "press_factors": Table(
            table="press_factors",
            source="test",
            rows=[{"name": "default", "stripping_force_pct": 10, "press_safety_factor": 1.25}],
        ),
        "strip_allowances": Table(
            table="strip_allowances",
            source="test",
            rows=[
                {
                    "thickness_mm_min": 1.0,
                    "thickness_mm_max": 2.0,
                    "bridge_mm": 2.0,
                    "edge_allowance_mm": 3.0,
                },
            ],
        ),
    }
)

RECT_50_30 = [(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (0.0, 30.0)]


def test_cutting_force_round_hole() -> None:
    """10 mm hole, 2 mm mild steel, tau 300 N/mm2.

    L = pi x 10 = 31.416 mm;  F = 31.416 x 2 x 300 = 18 849.6 N
    """
    assert cutting_force_n(math.pi * 10, 2.0, 300.0) == pytest.approx(18849.556, rel=1e-6)


def test_cutting_force_rectangle() -> None:
    """50 x 30 mm blank, 1.5 mm, tau 300.  L = 160 mm;  F = 160 x 1.5 x 300 = 72 000 N"""
    assert cutting_force_n(160.0, 1.5, 300.0) == pytest.approx(72000.0)


def test_stripping_force() -> None:
    """10 % of 72 000 N = 7 200 N"""
    assert stripping_force_n(72000.0, 10.0) == pytest.approx(7200.0)


def test_press_force() -> None:
    """(72 000 + 7 200) x 1.25 = 99 000 N = 99 kN"""
    assert press_force_kn(72000.0, 7200.0, 1.25) == pytest.approx(99.0)


def test_centre_of_pressure() -> None:
    """L1 = 100 at (0, 0), L2 = 50 at (30, 12).

    x = (100 x 0 + 50 x 30) / 150 = 10;  y = (50 x 12) / 150 = 4
    """
    contours = [
        CuttingContour(id="A", length_mm=100, centroid_x_mm=0, centroid_y_mm=0),
        CuttingContour(id="B", length_mm=50, centroid_x_mm=30, centroid_y_mm=12),
    ]
    assert centre_of_pressure_mm(contours) == pytest.approx((10.0, 4.0))


def test_polyline_contour_uses_line_centroid() -> None:
    """L-shaped outline (0,0)(20,0)(20,10)(10,10)(10,20)(0,20): perimeter 80 mm.

    Segment length @ midpoint: 20@(10,0) 10@(20,5) 10@(15,10) 10@(10,15) 10@(5,20) 20@(0,10)
    x = (20x10 + 10x20 + 10x15 + 10x10 + 10x5 + 20x0) / 80 = 700 / 80 = 8.75
    y = 8.75 by symmetry about the diagonal
    """
    contour = polyline_contour("L", [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)])
    assert contour.length_mm == pytest.approx(80.0)
    assert (contour.centroid_x_mm, contour.centroid_y_mm) == pytest.approx((8.75, 8.75))


def test_press_forces_from_standards() -> None:
    """Blank 50 x 30 plus one 10 mm hole at its centre, 1.5 mm mild steel.

    L = 160 + 31.416 = 191.416 mm;  F_cut = 191.416 x 1.5 x 300 = 86 137.2 N
    F_strip = 8 613.7 N;  F_press = 94 750.9 x 1.25 = 118 438.6 N = 118.44 kN
    Both contours are centred on (25, 15), so the centre of pressure is (25, 15).
    """
    contours = [polyline_contour("B1", RECT_50_30), circle_contour("H1", 10, 25, 15)]
    result = press_forces(contours, 1.5, "mild_steel", STANDARDS)

    assert result.cutting_force_n == pytest.approx(86137.17, rel=1e-5)
    assert result.stripping_force_n == pytest.approx(8613.72, rel=1e-5)
    assert result.press_force_kn == pytest.approx(118.4386, rel=1e-5)
    assert result.press_force_tonnes == pytest.approx(118438.6 / 9806.65, rel=1e-5)
    assert (result.centre_of_pressure_x_mm, result.centre_of_pressure_y_mm) == pytest.approx(
        (25.0, 15.0)
    )
    assert not result.uses_placeholder


def test_polygon_area() -> None:
    assert polygon_area_mm2(RECT_50_30) == pytest.approx(1500.0)


def test_rectangle_layout_at_0_degrees() -> None:
    """50 x 30 blank, bridge 2, edge allowance 3, fed along its 50 mm side.

    pitch = 50 + 2 = 52;  width = 30 + 2 x 3 = 36;  util = 1500 / (52 x 36) = 0.8013
    """
    layout = layout_at(RECT_50_30, 0.0, bridge_mm=2.0, edge_allowance_mm=3.0)
    assert layout.pitch_mm == pytest.approx(52.0, abs=2e-3)
    assert layout.strip_width_mm == pytest.approx(36.0)
    assert layout.utilisation == pytest.approx(1500 / (52 * 36), rel=1e-4)


def test_rectangle_best_rotation_is_90_degrees() -> None:
    """At 90 degrees: pitch = 30 + 2 = 32;  width = 50 + 6 = 56;  util = 1500 / 1792 = 0.8371,
    better than 0.8013 at 0 degrees."""
    options = strip_layout_options(RECT_50_30, 1.5, STANDARDS)
    assert options.at_0_deg.pitch_mm == pytest.approx(52.0, abs=2e-3)
    assert options.at_180_deg.pitch_mm == pytest.approx(52.0, abs=2e-3)
    assert options.best.angle_deg == pytest.approx(90.0)
    assert options.best.pitch_mm == pytest.approx(32.0, abs=2e-3)
    assert options.best.strip_width_mm == pytest.approx(56.0)
    assert options.best.utilisation == pytest.approx(1500 / (32 * 56), rel=1e-4)


def test_same_orientation_triangles_do_not_nest() -> None:
    """Right triangle (0,0)(40,0)(0,30), same orientation in every station.

    The copy's vertical edge x = p cannot slide under the hypotenuse because the bases
    are collinear: the base end (40, 0) and the copy's corner (p, 0) must be 2 mm
    apart, so pitch = 40 + 2 = 42 mm, the same as the bounding box.
    """
    layout = layout_at([(0, 0), (40, 0), (0, 30)], 0.0, bridge_mm=2.0, edge_allowance_mm=3.0)
    assert layout.pitch_mm == pytest.approx(42.0, abs=2e-3)


def test_chevron_nests_closer_than_its_bounding_box() -> None:
    """Chevron (arrowhead pointing +X): (0,0)(20,0)(30,10)(20,20)(0,20)(10,10).

    Its point at x 30 fits into the next chevron's notch at x 10, so the parts
    interlock. Same-shape copies are parallel, so the gap between the slanted edges
    is the X shift minus 20, measured perpendicular to edges at 45 degrees:
    distance = (p - 20) / sqrt(2) >= 2  ->  p = 20 + 2 sqrt(2) = 22.828 mm, less than
    the 30 + 2 = 32 mm bounding-box pitch. The straight top and bottom edges are
    collinear, not facing, so they do not limit the pitch.
    """
    chevron = [(0, 0), (20, 0), (30, 10), (20, 20), (0, 20), (10, 10)]
    layout = layout_at(chevron, 0.0, bridge_mm=2.0, edge_allowance_mm=3.0)
    assert layout.pitch_mm == pytest.approx(20 + 2 * math.sqrt(2), abs=2e-3)
