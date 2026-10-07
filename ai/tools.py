"""Tools the assistant can call: the designer, calculations, rules and standards.

Every number the assistant reports comes from one of these functions. Each tool has a
Pydantic input model; its JSON schema is what Claude sees, and the same model
validates what Claude sends back. Design changes are tried on a scratch copy of the
user's design: nothing here changes what the user sees until they apply a proposal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, create_model

from engine.calc.forces import CuttingContour, press_forces
from engine.calc.strip_layout import strip_layout_options
from rules.engine import RulesEngine, Subject, has_blocking_failure
from rules.standards import Cell, Standards, StandardsError
from server.designer import DesignInput, DesignResult, evaluate


class ToolError(Exception):
    """A tool could not run with the input it was given; the message goes back to Claude."""


@dataclass
class Session:
    """What the tools work on during one assistant turn."""

    design: DesignInput
    standards: Standards
    rules: RulesEngine
    proposal: DesignInput | None = None
    proposal_reason: str = ""
    proposal_result: DesignResult | None = None
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    is_error: bool


# --- inputs ---------------------------------------------------------------------------


def _optional_fields(model: type[BaseModel]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        assert info.annotation is not None
        fields[name] = (info.annotation | None, Field(None, description=info.description))
    return fields


# Every DesignInput field, optional: only the fields Claude wants to change. Values are
# validated against DesignInput's own limits when the changes are applied.
DesignChanges: type[BaseModel] = create_model(
    "DesignChanges",
    __doc__="Fields to change on the user's current design; leave out what stays the same.",
    **_optional_fields(DesignInput),
)


class EvaluateDesignInput(BaseModel):
    changes: DesignChanges = Field(
        description="Changes to the user's current design. Each call starts again from the "
        "user's design, so include every change you want evaluated together."
    )


class ProposeDesignInput(BaseModel):
    changes: DesignChanges = Field(
        description="The full set of changes you recommend, relative to the user's design"
    )
    reason: str = Field(description="One or two sentences on why, citing rule ids")


class CheckRulesInput(BaseModel):
    subjects: list[Subject] = Field(min_length=1)


class LookupStandardInput(BaseModel):
    table: str = Field(description="Standards table name")
    key: dict[str, Cell] = Field(
        description="Key values, e.g. {'material': 'mild_steel', 'thickness_mm': 2}. "
        "Range columns (thickness_mm_min/_max) are matched by the plain name."
    )


class ListRulesInput(BaseModel):
    pass


class PressForcesInput(BaseModel):
    contours: list[CuttingContour] = Field(min_length=1)
    thickness_mm: float = Field(gt=0)
    material: str


class StripLayoutInput(BaseModel):
    outline: list[tuple[float, float]] = Field(
        min_length=3, description="Blank outline in mm, first point not repeated"
    )
    thickness_mm: float = Field(gt=0)


# --- tools ----------------------------------------------------------------------------


def evaluate_design(session: Session, args: EvaluateDesignInput) -> dict[str, Any]:
    design = apply_changes(session.design, _changes(args.changes))
    return design_summary(evaluate(design, session.standards, session.rules))


def propose_design(session: Session, args: ProposeDesignInput) -> dict[str, Any]:
    changes = _changes(args.changes)
    if not changes:
        raise ToolError("A proposal needs at least one change")
    design = apply_changes(session.design, changes)
    result = evaluate(design, session.standards, session.rules)
    session.proposal, session.proposal_reason, session.proposal_result = (
        design,
        args.reason,
        result,
    )
    return {"recorded": True, "changes": changes, **design_summary(result)}


def check_rules(session: Session, args: CheckRulesInput) -> dict[str, Any]:
    results = session.rules.check(args.subjects)
    return {
        "results": [_rule_result(r) for r in results],
        "blocking": has_blocking_failure(results),
    }


def lookup_standard(session: Session, args: LookupStandardInput) -> dict[str, Any]:
    try:
        table = session.standards.table(args.table)
        row = table.lookup(args.key)
    except StandardsError as error:
        tables = sorted(session.standards.tables)
        raise ToolError(f"{error}. Tables: {', '.join(tables)}") from error
    return {
        "table": table.table,
        "source": table.source,
        "placeholder": table.placeholder,
        "row": row,
    }


def list_rules(session: Session, args: ListRulesInput) -> dict[str, Any]:
    return {
        "rules": [
            {
                "id": r.id,
                "title": r.title,
                "applies_to": r.applies_to,
                "check": r.check,
                "severity": r.severity,
                "source": r.source,
            }
            for r in session.rules.rules
        ],
        "tables": {
            name: {
                "description": t.description,
                "columns": sorted({k for row in t.rows for k in row}),
            }
            for name, t in session.standards.tables.items()
        },
    }


def calc_press_forces(session: Session, args: PressForcesInput) -> dict[str, Any]:
    forces = press_forces(args.contours, args.thickness_mm, args.material, session.standards)
    return _rounded_dict(forces.model_dump())


def calc_strip_layout(session: Session, args: StripLayoutInput) -> dict[str, Any]:
    options = strip_layout_options(args.outline, args.thickness_mm, session.standards)
    return _rounded_dict(options.model_dump())


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[Session, Any], dict[str, Any]]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema(self.input_model),
        }


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "evaluate_design",
            "Try changes on a scratch copy of the user's design and get the full result: "
            "blank area, edge distance and web, press forces, strip layouts, shut height, "
            "every rule result and input errors. Does not change the user's design.",
            EvaluateDesignInput,
            evaluate_design,
        ),
        Tool(
            "propose_design",
            "Record the design changes you recommend. The user sees them with an Apply "
            "button and the rule results for the proposed design. Evaluate first; call "
            "this at most once per answer, with the complete set of changes.",
            ProposeDesignInput,
            propose_design,
        ),
        Tool(
            "check_rules",
            "Check subjects (e.g. a pierce_punch with material, thickness_mm and "
            "diameter_mm) against the shop rules. Use list_rules for the kinds and values.",
            CheckRulesInput,
            check_rules,
        ),
        Tool(
            "lookup_standard",
            "Read one row of a shop standards table (materials, clearances, presses, die "
            "sets, strip allowances, screws, dowels and more).",
            LookupStandardInput,
            lookup_standard,
        ),
        Tool(
            "list_rules",
            "List every shop rule (id, what it applies to, its check, source) and every "
            "standards table with its columns.",
            ListRulesInput,
            list_rules,
        ),
        Tool(
            "press_forces",
            "Cutting, stripping and press force and the centre of pressure for cutting "
            "contours (perimeter length and centroid of each).",
            PressForcesInput,
            calc_press_forces,
        ),
        Tool(
            "strip_layout",
            "Single-row strip layouts for a blank outline: pitch, strip width and "
            "utilisation at 0 and 180 degrees and the best rotation.",
            StripLayoutInput,
            calc_strip_layout,
        ),
    ]
}


def run_tool(session: Session, name: str, raw_input: Any) -> ToolCall:
    """Validate the input, run the tool, and record the call; failures become errors."""
    raw = raw_input if isinstance(raw_input, dict) else {}
    tool = TOOLS.get(name)
    try:
        if tool is None:
            raise ToolError(f"Unknown tool {name!r}")
        output = tool.run(session, tool.input_model.model_validate(raw))
        call = ToolCall(name, raw, output, is_error=False)
    except ValidationError as error:
        call = ToolCall(name, raw, {"error": _validation_message(error)}, is_error=True)
    except (ToolError, StandardsError, ValueError) as error:
        call = ToolCall(name, raw, {"error": str(error)}, is_error=True)
    session.calls.append(call)
    return call


# --- helpers --------------------------------------------------------------------------


def input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with references inlined and titles dropped."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def apply_changes(design: DesignInput, changes: dict[str, Any]) -> DesignInput:
    try:
        return DesignInput.model_validate({**design.model_dump(), **changes})
    except ValidationError as error:
        raise ToolError(_validation_message(error)) from error


def design_summary(r: DesignResult) -> dict[str, Any]:
    """The result of a design evaluation, rounded the way the UI shows it."""
    summary: dict[str, Any] = {
        "errors": r.errors,
        "blank_area_mm2": r.blank_area_mm2,
        "min_edge_distance_mm": r.min_edge_distance_mm,
        "min_web_mm": r.min_web_mm,
    }
    if r.forces is not None:
        summary["forces"] = {
            "cutting_force_kn": r.forces.cutting_force_n / 1000,
            "stripping_force_kn": r.forces.stripping_force_n / 1000,
            "press_force_kn": r.forces.press_force_kn,
            "press_force_tonnes": r.forces.press_force_tonnes,
            "centre_of_pressure_mm": [
                r.forces.centre_of_pressure_x_mm,
                r.forces.centre_of_pressure_y_mm,
            ],
        }
    if r.layouts is not None:
        summary["strip_layout"] = {
            label: {
                "angle_deg": layout.angle_deg,
                "pitch_mm": layout.pitch_mm,
                "strip_width_mm": layout.strip_width_mm,
                "bridge_mm": layout.bridge_mm,
                "utilisation_pct": layout.utilisation * 100,
            }
            for label, layout in [
                ("best", r.layouts.best),
                ("at_0_deg", r.layouts.at_0_deg),
                ("at_180_deg", r.layouts.at_180_deg),
            ]
        }
    if r.stack is not None:
        summary["shut_height_mm"] = r.stack.shut_height_mm
    summary["rules"] = [_rule_result(x) for x in r.rules]
    summary["blocking"] = r.blocking
    summary["placeholder_tables"] = r.placeholder_tables
    return _rounded_dict(summary)


def _changes(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


def _rule_result(r: Any) -> dict[str, Any]:
    return _rounded_dict(r.model_dump())


def _rounded_dict(value: dict[str, Any]) -> dict[str, Any]:
    rounded: dict[str, Any] = _rounded(value)
    return rounded


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_rounded(v) for v in value]
    return value


def _validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in error.errors()
    )
