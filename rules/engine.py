"""Rules engine: checks design subjects (punches, plates, the strip) against YAML rules.

A rule file in standards/rules/ holds a list of rules::

    - id: PT-CLR-001
      title: Punch-die clearance per side
      applies_to: [pierce_punch, blank_punch]
      default:
        table: clearance_pct_by_material
        key: [material, thickness_mm]
      check: "clearance_pct between 3 and 10"
      severity: fail
      source: "Shop standard ST-02, section 4.1"

``default`` fills in values the subject does not give itself from a standards table
row (here the clearance for the subject's material and thickness). A rule that
cannot be checked because a value is missing is reported as a fail: an unchecked
design is never treated as a passing one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from rules.expression import Expression, ExpressionError, Value, parse
from rules.standards import DEFAULT_STANDARDS_DIR, Cell, Standards, StandardsError

Status = Literal["pass", "warning", "fail"]


class DefaultLookup(BaseModel):
    table: str
    key: list[str]


class Rule(BaseModel):
    id: str
    title: str
    applies_to: list[str]
    default: DefaultLookup | None = None
    check: str
    severity: Literal["warning", "fail"]
    source: str

    def expression(self) -> Expression:
        return parse(self.check)


class Subject(BaseModel):
    """Something to check: a feature, a plate, the strip layout, the whole tool."""

    kind: str = Field(description="Matches a rule's applies_to, e.g. pierce_punch")
    id: str = Field(description="Feature or component id, e.g. P1")
    values: dict[str, Cell]


class RuleResult(BaseModel):
    rule_id: str
    subject_id: str
    status: Status
    measured: dict[str, Cell] = Field(description="Values the check read")
    message: str
    source: str
    uses_placeholder: bool = Field(
        default=False, description="True if a value came from a placeholder standards table"
    )


class RulesEngine:
    def __init__(self, rules: Iterable[Rule], standards: Standards) -> None:
        self.rules = list(rules)
        self.standards = standards
        ids = [r.id for r in self.rules]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule ids: {duplicates}")
        for rule in self.rules:
            rule.expression()  # fail at load time on a malformed check

    @classmethod
    def load(cls, standards_dir: Path = DEFAULT_STANDARDS_DIR) -> RulesEngine:
        return cls(load_rules(standards_dir / "rules"), Standards.load(standards_dir))

    def check(self, subjects: Iterable[Subject]) -> list[RuleResult]:
        results: list[RuleResult] = []
        for subject in subjects:
            for rule in self.rules:
                if subject.kind in rule.applies_to:
                    results.append(self.check_one(rule, subject))
        return results

    def check_one(self, rule: Rule, subject: Subject) -> RuleResult:
        values: dict[str, Cell] = dict(subject.values)
        source = rule.source
        uses_placeholder = False
        expression = rule.expression()

        def failed(message: str) -> RuleResult:
            return RuleResult(
                rule_id=rule.id,
                subject_id=subject.id,
                status="fail",
                measured=_measured(expression, values),
                message=f"cannot check: {message}",
                source=source,
                uses_placeholder=uses_placeholder,
            )

        if rule.default is not None:
            missing = [k for k in rule.default.key if k not in values]
            if missing:
                return failed(f"{subject.id} has no value for {missing}")
            try:
                table = self.standards.table(rule.default.table)
                row = table.lookup({k: values[k] for k in rule.default.key})
            except StandardsError as error:
                return failed(str(error))
            source = f"{rule.source}; table {table.table}: {table.source}"
            uses_placeholder = table.placeholder
            for column, cell in row.items():
                values.setdefault(column, cell)

        numeric: dict[str, Value] = {
            k: v if isinstance(v, bool) else float(v)
            for k, v in values.items()
            if not isinstance(v, str)
        }
        try:
            outcome = expression.evaluate(numeric)
        except ExpressionError as error:
            return failed(str(error))
        if not isinstance(outcome, bool):
            return failed(f"check {rule.check!r} does not give true or false")

        status: Status = "pass" if outcome else rule.severity
        return RuleResult(
            rule_id=rule.id,
            subject_id=subject.id,
            status=status,
            measured=_measured(expression, values),
            message=f"{rule.title}: {'ok' if outcome else 'not met'} ({rule.check})",
            source=source,
            uses_placeholder=uses_placeholder,
        )


def load_rules(directory: Path) -> list[Rule]:
    rules: list[Rule] = []
    for path in sorted(directory.glob("*.yaml")):
        data: Any = yaml.safe_load(path.read_text()) or []
        rules.extend(Rule.model_validate(item) for item in data)
    return rules


def has_blocking_failure(results: Iterable[RuleResult]) -> bool:
    """A design with any fail cannot be released."""
    return any(r.status == "fail" for r in results)


def _measured(expression: Expression, values: Mapping[str, Cell]) -> dict[str, Cell]:
    return {name: values[name] for name in expression.names if name in values}
