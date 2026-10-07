"""STEP export and import."""

from __future__ import annotations

from pathlib import Path

import cadquery as cq


def export_step(shape: cq.Shape, path: Path) -> Path:
    """Write ``shape`` to a STEP file and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(cq.Workplane().add(shape), str(path), exportType="STEP")
    return path


def import_step(path: Path) -> cq.Shape:
    """Read a STEP file and return its contents as one shape."""
    shape = cq.importers.importStep(str(path)).val()
    if not isinstance(shape, cq.Shape):
        raise ValueError(f"{path} does not contain a shape")
    return shape
