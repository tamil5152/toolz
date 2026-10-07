"""Feature recognition on flat sheet-metal parts generated with CadQuery.

Each part is built, exported to STEP and loaded back, so the tests exercise the same
path as a customer's STEP file.
"""

from pathlib import Path

import cadquery as cq
import pytest

from engine.export.step import export_step
from engine.recognition.sheet_metal import (
    Notch,
    PartFeatures,
    RecognitionError,
    RectCutout,
    RoundHole,
    Slot,
    load_part,
)


def _load(part: cq.Workplane, tmp_path: Path) -> PartFeatures:
    return load_part(export_step(part.val(), tmp_path / "part.step"))


def test_plate_with_four_holes(tmp_path: Path) -> None:
    # 100 x 60 x 2 mm plate, four 6 mm holes on an 80 x 40 mm pattern, centred on origin.
    part = (
        cq.Workplane("XY")
        .box(100, 60, 2)
        .faces(">Z")
        .workplane()
        .rect(80, 40, forConstruction=True)
        .vertices()
        .hole(6)
    )
    result = _load(part, tmp_path)

    assert result.thickness_mm == pytest.approx(2.0)
    assert result.unrecognised == []
    assert len(result.outline) == 4
    assert result.outline_perimeter_mm == pytest.approx(2 * (100 + 60))

    holes = [f for f in result.features if isinstance(f, RoundHole)]
    assert len(holes) == 4
    assert len(result.features) == 4
    assert all(h.diameter_mm == pytest.approx(6.0) for h in holes)
    # Ids follow position: bottom row left to right, then top row.
    centres = {h.id: (h.centre.x_mm, h.centre.y_mm) for h in holes}
    assert centres == {
        "H1": (-40.0, -20.0),
        "H2": (40.0, -20.0),
        "H3": (-40.0, 20.0),
        "H4": (40.0, 20.0),
    }


def test_plate_with_slot(tmp_path: Path) -> None:
    # 80 x 50 x 3 mm plate with a 30 x 8 mm slot at (10, 5), rotated 30 degrees.
    part = (
        cq.Workplane("XY")
        .box(80, 50, 3)
        .faces(">Z")
        .workplane()
        .center(10, 5)
        .slot2D(30, 8, 30)
        .cutThruAll()
    )
    result = _load(part, tmp_path)

    assert result.thickness_mm == pytest.approx(3.0)
    assert result.unrecognised == []
    assert len(result.features) == 1
    slot = result.features[0]
    assert isinstance(slot, Slot)
    assert slot.id == "S1"
    assert slot.length_mm == pytest.approx(30.0)
    assert slot.width_mm == pytest.approx(8.0)
    assert slot.angle_deg == pytest.approx(30.0)
    assert (slot.centre.x_mm, slot.centre.y_mm) == pytest.approx((10.0, 5.0))


def test_l_shaped_plate_with_notch(tmp_path: Path) -> None:
    # L-shape: 60 x 60 mm square with the 30 x 30 mm top-right corner removed,
    # 1.5 mm thick, plus a 10 mm wide, 5 mm deep notch in the bottom edge.
    outline = [
        (0, 0),
        (20, 0),
        (20, 5),
        (30, 5),
        (30, 0),
        (60, 0),
        (60, 30),
        (30, 30),
        (30, 60),
        (0, 60),
    ]
    part = cq.Workplane("XY").polyline(outline).close().extrude(1.5)
    result = _load(part, tmp_path)

    assert result.thickness_mm == pytest.approx(1.5)
    assert result.unrecognised == []
    notches = sorted((f for f in result.features if isinstance(f, Notch)), key=lambda n: n.id)
    assert len(notches) == 2
    edge_notch, corner_notch = notches  # N1 is lower (y = 0), N2 is the L corner
    assert edge_notch.id == "N1"
    assert edge_notch.opening_width_mm == pytest.approx(10.0)
    assert edge_notch.depth_mm == pytest.approx(5.0)
    assert (edge_notch.opening_centre.x_mm, edge_notch.opening_centre.y_mm) == pytest.approx(
        (25.0, 0.0)
    )
    # The L corner is measured against the hull edge from (60, 30) to (30, 60).
    assert corner_notch.id == "N2"
    assert corner_notch.opening_width_mm == pytest.approx(30 * 2**0.5)
    assert corner_notch.depth_mm == pytest.approx(15 * 2**0.5)


def test_rectangular_cutout_with_corner_radius(tmp_path: Path) -> None:
    part = (
        cq.Workplane("XY")
        .box(100, 60, 2)
        .faces(">Z")
        .workplane()
        .sketch()
        .rect(40, 20)
        .vertices()
        .fillet(3)
        .finalize()
        .cutThruAll()
    )
    result = _load(part, tmp_path)

    assert result.unrecognised == []
    assert len(result.features) == 1
    cutout = result.features[0]
    assert isinstance(cutout, RectCutout)
    assert cutout.id == "R1"
    assert cutout.length_mm == pytest.approx(40.0)
    assert cutout.width_mm == pytest.approx(20.0)
    assert cutout.corner_radius_mm == pytest.approx(3.0)
    assert cutout.angle_deg == pytest.approx(0.0)


def test_unknown_contour_is_reported_not_guessed(tmp_path: Path) -> None:
    part = (
        cq.Workplane("XY")
        .box(100, 60, 2)
        .faces(">Z")
        .workplane()
        .polyline([(-10, -10), (10, -10), (0, 10)])
        .close()
        .cutThruAll()
    )
    result = _load(part, tmp_path)

    assert result.features == []
    assert len(result.unrecognised) == 1
    assert "not a round hole, slot or rectangle" in result.unrecognised[0]


def test_part_that_is_not_flat_is_flagged(tmp_path: Path) -> None:
    # A stepped block is not a constant-thickness blank.
    part = cq.Workplane("XY").box(100, 60, 2).faces(">Z").workplane().rect(20, 20).extrude(5)
    result = _load(part, tmp_path)

    assert any("not a flat plate" in note for note in result.unrecognised)


def test_more_than_one_solid_is_rejected() -> None:
    from engine.recognition.sheet_metal import recognise

    two = (
        cq.Workplane("XY")
        .box(10, 10, 1)
        .union(cq.Workplane("XY").box(10, 10, 1).translate((50, 0, 0)))
    )
    with pytest.raises(RecognitionError):
        recognise(two.val())


def test_rounded_outline_corners_are_not_notches(tmp_path: Path) -> None:
    part = cq.Workplane("XY").box(80, 40, 2).edges("|Z").fillet(5)
    result = _load(part, tmp_path)

    assert result.features == []
    assert result.unrecognised == []
    assert len(result.outline) > 4  # arcs are sampled into the polyline
