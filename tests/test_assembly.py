"""A complete simple press tool built from its feature tree, and the assembly checks.

Uses the fixed standards in tests/fixtures/standards.
"""

from pathlib import Path

import pytest

from engine.features.assembly import BuiltAssembly, ToolAssembly
from engine.features.base import ToolSpec
from engine.validity import is_valid_solid
from rules.engine import RuleResult, RulesEngine, has_blocking_failure
from rules.standards import Standards

FIXTURES = Path(__file__).parent / "fixtures" / "standards"
STANDARDS = Standards.load(FIXTURES)
RULES = RulesEngine.load(FIXTURES)

SPEC = ToolSpec(
    material="mild_steel",
    sheet_thickness_mm=1.5,
    press_id="PR-400",
    die_set_id="DS-250x200",
    die_plate_thickness_mm=25,
    stripper_gap_mm=2,
    stripper_thickness_mm=12,
    punch_holder_thickness_mm=20,
    back_plate_thickness_mm=6,
    punch_length_mm=60,
    die_penetration_mm=1,
    stripper_clearance_per_side_mm=0.5,
    slug_relief_per_side_mm=0.5,
)
PLATE = {"length_mm": 160, "width_mm": 120}
CORNERS = [{"x_mm": x, "y_mm": y} for x in (-65, 65) for y in (-45, 45)]
SIDES = [{"x_mm": -65, "y_mm": 0}, {"x_mm": 65, "y_mm": 0}]


def simple_tool(spec: ToolSpec = SPEC) -> ToolAssembly:
    """Pierce a 6 mm hole, then blank a 50 x 30 mm part, with one pilot."""
    tool = ToolAssembly(spec)
    tool.add("die_set", {})
    for plate in ("die_plate", "stripper_plate", "punch_holder_plate", "back_plate"):
        tool.add(plate, PLATE)
    tool.add(
        "pierce_punch",
        {
            "id": "P1",
            "x_mm": -30,
            "y_mm": 0,
            "diameter_mm": 6,
            "head_diameter_mm": 10,
            "head_thickness_mm": 5,
            "button_outer_diameter_mm": 16,
        },
    )
    tool.add(
        "blank_punch",
        {"id": "B1", "outline": [(0, 0), (50, 0), (50, 30), (0, 30)], "x_mm": 5, "y_mm": -15},
    )
    tool.add("pilot", {"id": "PL1", "x_mm": -50, "y_mm": 20, "diameter_mm": 5})
    tool.add("screws", {"id": "SCL", "size": "M8", "side": "lower", "positions": CORNERS})
    tool.add("screws", {"id": "SCU", "size": "M8", "side": "upper", "positions": CORNERS})
    tool.add("dowels", {"id": "DWL", "diameter_mm": 8, "side": "lower", "positions": SIDES})
    tool.add("dowels", {"id": "DWU", "diameter_mm": 8, "side": "upper", "positions": SIDES})
    return tool


@pytest.fixture(scope="module")
def built() -> BuiltAssembly:
    return simple_tool().build(STANDARDS)


@pytest.fixture(scope="module")
def results(built: BuiltAssembly) -> list[RuleResult]:
    return built.check(RULES)


def by_rule(results: list[RuleResult], rule_id: str) -> list[RuleResult]:
    return [r for r in results if r.rule_id == rule_id]


def test_simple_tool_has_every_component(built: BuiltAssembly) -> None:
    ids = set(built.components)
    assert {"LS", "US", "G1", "G2", "G3", "G4", "GB1", "GB2", "GB3", "GB4"} <= ids
    assert {"DP", "SP", "PH", "BP", "P1", "P1-DB", "B1", "B1-DO", "PL1"} <= ids
    assert {f"SCL-{i}" for i in range(1, 5)} <= ids
    assert {"DWU-1", "DWU-2"} <= ids
    assert "B1-DO" not in {c.id for c in built.parts}  # virtual


def test_every_part_is_a_valid_solid(built: BuiltAssembly) -> None:
    for c in built.parts:
        assert is_valid_solid(c.solid), c.id


def test_simple_tool_passes_every_check(results: list[RuleResult]) -> None:
    failures = [r for r in results if r.status != "pass"]
    assert failures == []
    assert not has_blocking_failure(results)


def test_shut_height(built: BuiltAssembly, results: list[RuleResult]) -> None:
    """Lower shoe 40 + die plate 25 + punch length 60 - penetration 1 + back plate 6
    + upper shoe 35 = 165 mm, inside the fixture press range 150-200."""
    assert built.stack.shut_height_mm == pytest.approx(165.0)
    [shut] = by_rule(results, "PT-SH-001")
    assert shut.status == "pass"
    assert shut.measured["shut_height_mm"] == pytest.approx(165.0)


def test_punches_are_aligned_with_die_clearance(results: list[RuleResult]) -> None:
    """Fixture clearance for 1.5 mm mild steel is 5 %: 0.075 mm per side."""
    align = {r.subject_id: r for r in by_rule(results, "GEO-ALIGN")}
    assert set(align) == {"P1", "B1"}
    for result in align.values():
        assert result.status == "pass"
        assert result.measured["gap_mm"] == pytest.approx(0.075, abs=1e-3)


def test_holes_are_cut_into_plates(built: BuiltAssembly) -> None:
    """Die plate 160 x 120 x 25 minus: button bore 16, blank opening 50.15 x 30.15 with
    0.075 corner radii, pilot relief 6, 2 lower dowels 8, and 4 screw threads 8 x 12."""
    import math

    plate = 160 * 120 * 25
    button = math.pi * 8**2 * 25
    opening = (50.15 * 30.15 - (4 - math.pi) * 0.075**2) * 25
    relief = math.pi * 3**2 * 25
    dowel = 2 * math.pi * 4**2 * 25
    screw = 4 * math.pi * 4**2 * 12
    expected = plate - button - opening - relief - dowel - screw
    assert built.components["DP"].solid.Volume() == pytest.approx(expected, rel=1e-6)


def test_feature_tree_round_trips_through_json(built: BuiltAssembly) -> None:
    text = simple_tool().to_json()
    rebuilt = ToolAssembly.from_json(text).build(STANDARDS)
    assert rebuilt.components.keys() == built.components.keys()
    for cid, component in built.components.items():
        assert rebuilt.components[cid].solid.Volume() == pytest.approx(component.solid.Volume())


def test_clash_is_found() -> None:
    # A second pierce punch whose die button sits on the lower dowel at (65, 0).
    tool = simple_tool()
    tool.add(
        "pierce_punch",
        {
            "id": "P2",
            "x_mm": 65,
            "y_mm": 0,
            "diameter_mm": 6,
            "head_diameter_mm": 10,
            "head_thickness_mm": 5,
            "button_outer_diameter_mm": 16,
        },
    )
    results = tool.build(STANDARDS).check(RULES)
    clashes = {r.subject_id for r in by_rule(results, "GEO-CLASH")}
    assert "P2-DB/DWL-2" in clashes or "DWL-2/P2-DB" in clashes
    assert has_blocking_failure(results)


def test_misaligned_die_is_found() -> None:
    built = simple_tool().build(STANDARDS)
    button = built.components["P1-DB"]
    button.solid = button.solid.translate((0.05, 0, 0))
    [p1] = [r for r in built.check_punch_die_alignment() if r.subject_id == "P1"]
    assert p1.status == "fail"
    assert p1.measured["gap_mm"] == pytest.approx(0.025, abs=1e-3)


def test_shut_height_outside_press_range_fails() -> None:
    tool = simple_tool(SPEC.model_copy(update={"punch_length_mm": 100}))
    [shut] = by_rule(tool.build(STANDARDS).check(RULES), "PT-SH-001")
    assert shut.status == "fail"
    assert shut.measured["shut_height_mm"] == pytest.approx(205.0)


def test_screw_too_close_to_plate_edge_fails() -> None:
    tool = simple_tool()
    # 80 - 72 = 8 mm from the die plate edge; the fixture needs 1.5 x 8 = 12 mm.
    tool.add(
        "screws",
        {"id": "SCX", "size": "M8", "side": "lower", "positions": [{"x_mm": 72, "y_mm": -20}]},
    )
    results = tool.build(STANDARDS).check(RULES)
    [edge] = [r for r in by_rule(results, "PT-FAS-001") if r.subject_id == "SCX-1"]
    assert edge.status == "fail"
    assert edge.measured["edge_distance_mm"] == pytest.approx(8.0)


def test_missing_plate_is_reported() -> None:
    tool = ToolAssembly(SPEC)
    tool.add("die_set", {})
    tool.add("die_plate", PLATE)
    tool.add(
        "pierce_punch",
        {
            "id": "P1",
            "x_mm": 0,
            "y_mm": 0,
            "diameter_mm": 6,
            "head_diameter_mm": 10,
            "head_thickness_mm": 5,
            "button_outer_diameter_mm": 16,
        },
    )
    results = tool.build(STANDARDS).check(RULES)
    missing = {r.message for r in by_rule(results, "GEO-CUT")}
    assert any("PH" in m for m in missing)
    assert any("SP" in m for m in missing)


def test_invalid_feature_parameters_are_rejected() -> None:
    tool = ToolAssembly(SPEC)
    with pytest.raises(ValueError):
        tool.add("pierce_punch", {"id": "P1", "x_mm": 0, "y_mm": 0, "diameter_mm": -6})
    with pytest.raises(ValueError, match="unknown feature"):
        tool.add("laser_beam", {})


def test_assembly_exports_to_step(built: BuiltAssembly, tmp_path: Path) -> None:
    path = built.to_step(tmp_path / "tool.step")
    assert path.stat().st_size > 10_000
