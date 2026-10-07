"""OpenCASCADE validity checks applied to every solid the engine builds."""

from __future__ import annotations

import cadquery as cq
from OCP.BRepCheck import BRepCheck_Analyzer


def is_valid_solid(shape: cq.Shape) -> bool:
    """True when ``shape`` passes BRepCheck_Analyzer and contains at least one solid."""
    if not BRepCheck_Analyzer(shape.wrapped).IsValid():
        return False
    return len(shape.Solids()) > 0
