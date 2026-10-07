"""The live designer behind the web UI: inputs to blank, forces, layout, stack and rules.

Uses the fixed standards in tests/fixtures/standards.
"""

import math
from pathlib import Path

import pytest

from engine.calc.strip_layout import polygon_area_mm2
from rules.engine import RulesEngine
from rules.standards import Standards
from server.designer import DesignInput, blank_outline, evaluate, parse_form, parse_holes
from server.drawings import blank_svg, stack_elevation_svg, stack_iso_svg, strip_svg
from server.ui import results

FIXTURES = Path(__file__).parent / "fixtures" / "standards"
STANDARDS = Standards.load(FIXTURES)
RULES = RulesEngine.load(FIXTURES)


def statuses(inputs: DesignInput) -> dict[tuple[str, str], str]:
    result = evaluate(inputs, STANDARDS, RULES)
    return {(r.rule_id, r.subject_id): r.status for r in result.rules}


def test_rectangle_outline_area() -> None:
    """60 x 40 with 4 mm corners: 2400 - (4 - pi) x 16 = 2386.27 mm2 (polyline within 0.1 %)."""
    outline = blank_outline(DesignInput(length_mm=60, width_mm=40, corner_radius_mm=4))
    assert polygon_area_mm2(outline) == pytest.approx(2400 - (4 - math.pi) * 16, rel=1e-3)


def test_circle_outline_area() -> None:
    outline = blank_outline(DesignInput(shape="circle", diameter_mm=50))
    assert polygon_area_mm2(outline) == pytest.approx(math.pi * 25**2, rel=3e-3)


def test_custom_outline_and_holes_parse() -> None:
    outline = blank_outline(DesignInput(shape="custom", custom_points="0,0\n10 0\n10;5"))
    assert outline == [(0, 0), (10, 0), (10, 5)]
    holes = parse_holes("6, 10, 20\n\n8 30 20")
    assert [(h.diameter_mm, h.x_mm, h.y_mm) for h in holes] == [(6, 10, 20), (8, 30, 20)]
    with pytest.raises(ValueError, match="Line 1"):
        parse_holes("6, 10")


def test_default_design_evaluates_completely() -> None:
    result = evaluate(DesignInput(), STANDARDS, RULES)
    assert result.errors == []
    assert result.forces is not None and result.forces.press_force_kn > 0
    assert result.layouts is not None
    assert result.stack is not None
    assert result.min_web_mm == pytest.approx(30 - 8)  # holes 30 mm apart, 8 mm each
    assert result.min_edge_distance_mm == pytest.approx(15 - 4)  # hole centre 15 mm from edge
    rule_ids = {r.rule_id for r in result.rules}
    assert {
        "PT-CLR-001",
        "PT-MIN-004",
        "PT-EDGE-001",
        "PT-WEB-001",
        "PT-TON-001",
        "PT-SH-001",
    } <= rule_ids


def test_shut_height_follows_the_stack() -> None:
    """Lower shoe 40 + die 25 + punch 80 - penetration 1 + back plate 6 + upper shoe 35 = 185;
    the fixture press allows 150-200."""
    result = evaluate(DesignInput(punch_length_mm=80), STANDARDS, RULES)
    assert result.stack is not None
    assert result.stack.shut_height_mm == pytest.approx(185)
    assert statuses(DesignInput(punch_length_mm=80))[("PT-SH-001", "TOOL")] == "pass"
    assert statuses(DesignInput(punch_length_mm=120))[("PT-SH-001", "TOOL")] == "fail"


def test_overloaded_press_fails() -> None:
    big = DesignInput(length_mm=400, width_mm=300, thickness_mm=5.5, material="stainless_304")
    assert statuses(big)[("PT-TON-001", "PRESS")] == "fail"


def test_hole_too_close_to_edge_fails() -> None:
    near = DesignInput(holes="8, 5, 20")  # 5 - 4 = 1 mm from the edge
    assert statuses(near)[("PT-EDGE-001", "PART")] == "fail"


def test_hole_outside_blank_is_an_input_error() -> None:
    result = evaluate(DesignInput(holes="8, 100, 20"), STANDARDS, RULES)
    assert any("outside the blank" in e for e in result.errors)
    assert result.blocking


def test_holder_below_stripper_is_an_input_error() -> None:
    result = evaluate(DesignInput(punch_length_mm=30), STANDARDS, RULES)
    assert any("Punch holder bottom" in e for e in result.errors)


def test_bad_form_values_fall_back_with_an_error() -> None:
    inputs, errors = parse_form({"thickness_mm": "abc", "length_mm": "80"})
    assert inputs.length_mm == 80
    assert inputs.thickness_mm == DesignInput().thickness_mm
    assert errors and errors[0].startswith("thickness_mm")


def test_drawings_and_results_render() -> None:
    result = evaluate(DesignInput(), STANDARDS, RULES)
    assert result.layouts is not None and result.stack is not None
    for svg in (
        blank_svg(result.outline, result.holes),
        strip_svg(result.outline, result.holes, result.layouts.best),
        stack_elevation_svg(result.stack, result.inputs, result.die_set),
        stack_iso_svg(result.stack, result.inputs, result.die_set),
    ):
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "nan" not in svg
    html = results(result)
    assert "Rule checks" in html and "Strip layout" in html


def test_user_text_is_escaped_in_results() -> None:
    result = evaluate(DesignInput(holes="<script>alert(1)</script>"), STANDARDS, RULES)
    html = results(result)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
