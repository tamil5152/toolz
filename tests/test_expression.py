"""The rule expression evaluator: arithmetic, comparisons, logic, and rejection of
anything that is not part of the small language."""

import pytest

from rules.expression import ExpressionError, evaluate, parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 + 2 * 3", 7.0),
        ("(1 + 2) * 3", 9.0),
        ("10 / 4 - 1", 1.5),
        ("-2 * -3", 6.0),
        ("2 < 3", True),
        ("3 <= 3", True),
        ("3 > 4", False),
        ("2 == 2.0", True),
        ("2 != 2", False),
        ("5 between 3 and 10", True),
        ("3 between 3 and 10", True),
        ("10.5 between 3 and 10", False),
        ("1 < 2 and 3 < 2", False),
        ("1 < 2 or 3 < 2", True),
        ("not 1 > 2", True),
        ("true and not false", True),
        ("5 between 3 and 10 and 1 < 2", True),
    ],
)
def test_constant_expressions(text: str, expected: float | bool) -> None:
    assert evaluate(text, {}) == expected


def test_field_names() -> None:
    values = {"diameter_mm": 3.0, "thickness_mm": 2.0, "min_diameter_to_thickness": 1.0}
    assert evaluate("diameter_mm >= min_diameter_to_thickness * thickness_mm", values) is True
    values["diameter_mm"] = 1.5
    assert evaluate("diameter_mm >= min_diameter_to_thickness * thickness_mm", values) is False


def test_names_lists_fields_in_order() -> None:
    expr = parse("diameter_mm >= factor * thickness_mm and diameter_mm < 50")
    assert expr.names == ["diameter_mm", "factor", "thickness_mm"]


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "a.__class__",  # attribute access is just a name; it must not resolve
        "1 +",
        "(1 + 2",
        "1 2",
        "x[0]",
        "3 between 1",
        "'text' == 1",
    ],
)
def test_rejects_anything_outside_the_language(text: str) -> None:
    with pytest.raises(ExpressionError):
        evaluate(text, {})


def test_missing_field_is_an_error_not_zero() -> None:
    with pytest.raises(ExpressionError, match="no value for 'clearance_pct'"):
        evaluate("clearance_pct > 3", {})


def test_division_by_zero_is_an_error() -> None:
    with pytest.raises(ExpressionError, match="division by zero"):
        evaluate("1 / (2 - 2)", {})


def test_type_mismatch_is_an_error() -> None:
    with pytest.raises(ExpressionError):
        evaluate("(1 < 2) + 1", {})
    with pytest.raises(ExpressionError):
        evaluate("1 and 2", {})
