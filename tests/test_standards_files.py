"""The standards and rules shipped in standards/ load and fit together."""

import pytest

from rules.engine import RulesEngine
from rules.standards import Standards

REQUIRED_TABLES = {
    "materials",
    "clearance_pct_by_material",
    "min_punch_diameter",
    "min_web_and_edge",
}


def test_shipped_rules_and_tables_load() -> None:
    engine = RulesEngine.load()
    assert engine.rules
    assert set(engine.standards.tables) >= REQUIRED_TABLES


def test_every_rule_default_table_exists() -> None:
    engine = RulesEngine.load()
    for rule in engine.rules:
        if rule.default is not None:
            engine.standards.table(rule.default.table)


def test_material_strengths_are_positive() -> None:
    standards = Standards.load()
    for row in standards.table("materials").rows:
        assert float(row["shear_strength_n_per_mm2"]) > 0  # type: ignore[arg-type]


@pytest.mark.needs_shop_standards
def test_no_placeholder_standards_left() -> None:
    """Fails until every table in standards/ holds the shop's own values."""
    placeholders = Standards.load().placeholder_tables
    assert placeholders == [], (
        f"Replace the PLACEHOLDER values in these standards tables: {placeholders}"
    )
