"""Smoke test: the geometry stack builds, validates and exports a solid."""

from pathlib import Path

import cadquery as cq
import pytest

from engine.export.step import export_step, import_step
from engine.validity import is_valid_solid


def test_box_is_valid_and_round_trips_through_step(tmp_path: Path) -> None:
    box = cq.Workplane("XY").box(100, 80, 20).val()

    assert is_valid_solid(box)
    assert box.Volume() == pytest.approx(100 * 80 * 20)

    step_file = export_step(box, tmp_path / "box.step")
    assert step_file.stat().st_size > 0

    reloaded = import_step(step_file)
    assert is_valid_solid(reloaded)
    assert reloaded.Volume() == pytest.approx(100 * 80 * 20, rel=1e-6)
