"""The rules engine against a small standards set written in the test, so the tests
do not depend on the (placeholder) values shipped in standards/."""

from pathlib import Path

import pytest

from rules.engine import Rule, RulesEngine, Subject, has_blocking_failure
from rules.standards import Standards, StandardsError, Table

CLEARANCE = Table(
    table="clearance_pct_by_material",
    source="Test standard T-1",
    rows=[
        {
            "material": "mild_steel",
            "thickness_mm_min": 0.0,
            "thickness_mm_max": 3.0,
            "clearance_pct": 5,
        },
        {
            "material": "mild_steel",
            "thickness_mm_min": 3.0,
            "thickness_mm_max": 6.0,
            "clearance_pct": 12,
        },
    ],
)
MIN_DIAMETER = Table(
    table="min_punch_diameter",
    source="Test standard T-2",
    placeholder=True,
    rows=[{"material": "mild_steel", "min_diameter_to_thickness": 1.0}],
)

CLEARANCE_RULE = Rule(
    id="PT-CLR-001",
    title="Punch-die clearance per side",
    applies_to=["pierce_punch", "blank_punch"],
    default={"table": "clearance_pct_by_material", "key": ["material", "thickness_mm"]},
    check="clearance_pct between 3 and 10",
    severity="fail",
    source="Shop standard ST-02, section 4.1",
)
DIAMETER_RULE = Rule(
    id="PT-MIN-004",
    title="Minimum punch diameter",
    applies_to=["pierce_punch"],
    default={"table": "min_punch_diameter", "key": ["material"]},
    check="diameter_mm >= min_diameter_to_thickness * thickness_mm",
    severity="warning",
    source="Piercing limits",
)


@pytest.fixture
def engine() -> RulesEngine:
    standards = Standards({t.table: t for t in (CLEARANCE, MIN_DIAMETER)})
    return RulesEngine([CLEARANCE_RULE, DIAMETER_RULE], standards)


def punch(diameter_mm: float, thickness_mm: float, **extra: float | str) -> Subject:
    values: dict[str, float | str] = {
        "material": "mild_steel",
        "thickness_mm": thickness_mm,
        "diameter_mm": diameter_mm,
    }
    values.update(extra)
    return Subject(kind="pierce_punch", id="P1", values=values)


def test_passing_punch(engine: RulesEngine) -> None:
    results = engine.check([punch(diameter_mm=6, thickness_mm=2)])

    assert [(r.rule_id, r.status) for r in results] == [
        ("PT-CLR-001", "pass"),
        ("PT-MIN-004", "pass"),
    ]
    clearance = results[0]
    assert clearance.measured == {"clearance_pct": 5}
    assert "Shop standard ST-02" in clearance.source
    assert "Test standard T-1" in clearance.source
    assert not clearance.uses_placeholder
    assert results[1].uses_placeholder
    assert not has_blocking_failure(results)


def test_value_from_table_range_can_fail(engine: RulesEngine) -> None:
    # 4 mm falls in the 3-6 mm row, whose 12% is outside the 3-10% limit.
    [clearance, _] = engine.check([punch(diameter_mm=6, thickness_mm=4)])
    assert clearance.status == "fail"
    assert clearance.measured == {"clearance_pct": 12}


def test_subject_value_overrides_table_default(engine: RulesEngine) -> None:
    [clearance, _] = engine.check([punch(diameter_mm=6, thickness_mm=4, clearance_pct=8)])
    assert clearance.status == "pass"
    assert clearance.measured == {"clearance_pct": 8}


def test_warning_severity(engine: RulesEngine) -> None:
    [_, diameter] = engine.check([punch(diameter_mm=1.5, thickness_mm=2)])
    assert diameter.status == "warning"
    assert diameter.measured == {
        "diameter_mm": 1.5,
        "min_diameter_to_thickness": 1.0,
        "thickness_mm": 2,
    }
    assert not has_blocking_failure([diameter])


def test_missing_data_fails_instead_of_passing(engine: RulesEngine) -> None:
    subject = Subject(kind="pierce_punch", id="P2", values={"material": "mild_steel"})
    results = engine.check([subject])
    assert all(r.status == "fail" for r in results)
    assert "cannot check" in results[0].message
    assert has_blocking_failure(results)


def test_no_table_row_fails(engine: RulesEngine) -> None:
    [clearance, _] = engine.check([punch(diameter_mm=6, thickness_mm=2, material="titanium")])
    assert clearance.status == "fail"
    assert "no row" in clearance.message


def test_rules_only_apply_to_their_kinds(engine: RulesEngine) -> None:
    blank = Subject(
        kind="blank_punch", id="B1", values={"material": "mild_steel", "thickness_mm": 2}
    )
    results = engine.check([blank])
    assert [r.rule_id for r in results] == ["PT-CLR-001"]


def test_malformed_check_is_rejected_at_load() -> None:
    bad = CLEARANCE_RULE.model_copy(update={"check": "clearance_pct between 3"})
    with pytest.raises(ValueError):
        RulesEngine([bad], Standards({}))


def test_duplicate_rule_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        RulesEngine([CLEARANCE_RULE, CLEARANCE_RULE], Standards({}))


def test_ambiguous_table_rows_are_an_error() -> None:
    table = Table(
        table="t",
        source="s",
        rows=[{"material": "a", "x": 1}, {"material": "a", "x": 2}],
    )
    with pytest.raises(StandardsError, match="2 rows"):
        table.lookup({"material": "a"})


def test_load_from_files(tmp_path: Path) -> None:
    (tmp_path / "rules").mkdir()
    (tmp_path / "clearance.yaml").write_text(
        "table: clearance_pct_by_material\n"
        "source: Test\n"
        "rows:\n"
        "  - {material: mild_steel, thickness_mm_min: 0, thickness_mm_max: 3, clearance_pct: 5}\n"
    )
    (tmp_path / "rules" / "r.yaml").write_text(
        "- id: R1\n"
        "  title: Clearance\n"
        "  applies_to: [pierce_punch]\n"
        "  default: {table: clearance_pct_by_material, key: [material, thickness_mm]}\n"
        "  check: clearance_pct between 3 and 10\n"
        "  severity: fail\n"
        "  source: Test\n"
    )
    engine = RulesEngine.load(tmp_path)
    [result] = engine.check([punch(diameter_mm=5, thickness_mm=1)])
    assert result.status == "pass"
