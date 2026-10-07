"""Web API for the tool design engine (FastAPI). Vercel loads ``app`` from here.

Rules, standards and press calculations need only the light dependencies and run
anywhere. Part recognition and tool building need CadQuery (the ``cad`` extra), which
is too large for a standard Vercel function; without it those endpoints answer 503.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ai import assistant
from engine.calc.forces import CuttingContour, PressForces, press_forces
from engine.calc.strip_layout import StripLayoutOptions, strip_layout_options
from rules.engine import RuleResult, RulesEngine, Subject, has_blocking_failure
from rules.standards import Standards, StandardsError
from server import assistant_ui, ui
from server.designer import DesignInput, DesignResult, evaluate, parse_form

app = FastAPI(
    title="Tooldesign API",
    description="AI-assisted press tool design: shop rules, press calculations and geometry.",
    version="0.1.0",
)

CAD_AVAILABLE = importlib.util.find_spec("cadquery") is not None


def _standards() -> Standards:
    return Standards.load()


def _require_cad() -> None:
    if not CAD_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Geometry is not available on this server: CadQuery is not installed. "
            "Run the engine locally with the 'cad' extra for part import and tool building.",
        )


# --- pages ----------------------------------------------------------------------------


def _design(form: dict[str, str]) -> tuple[DesignInput, DesignResult, Standards]:
    standards = _standards()
    inputs, errors = parse_form(form)
    result = evaluate(inputs, standards, RulesEngine.load())
    result.errors = errors + result.errors
    return inputs, result, standards


async def _form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    inputs, result, standards = _design({})
    chat = assistant_ui.panel([], assistant.configured(), inputs)
    return ui.page(inputs, result, standards, CAD_AVAILABLE, chat)


@app.post("/", response_class=HTMLResponse, include_in_schema=False)
async def home_submit(request: Request) -> str:
    """Plain form submit (browsers without htmx), and applying an assistant proposal."""
    form = await _form(request)
    inputs, result, standards = _design(form)
    history = assistant_ui.parse_history(form.get("history", ""))
    chat = assistant_ui.panel(history, assistant.configured(), inputs)
    return ui.page(inputs, result, standards, CAD_AVAILABLE, chat)


@app.post("/ui/update", response_class=HTMLResponse, include_in_schema=False)
async def ui_update(request: Request) -> str:
    """The results panel for the current inputs; the page swaps it in on every change."""
    _, result, _ = _design(await _form(request))
    return ui.results(result)


@app.post("/ui/assistant", response_class=HTMLResponse, include_in_schema=False)
async def ui_assistant(request: Request) -> str:
    """Answer a chat question about the design in the form; returns the chat panel."""
    form = await _form(request)
    history = assistant_ui.parse_history(form.get("history", ""))
    message = form.get("message", "").strip()
    inputs, _ = parse_form(form)
    if not assistant.configured() or not message:
        return assistant_ui.panel(history, assistant.configured(), inputs)
    reply = assistant.ask(message, inputs, history, _standards(), RulesEngine.load())
    if reply.error:
        return assistant_ui.panel(history, True, inputs, reply, message=message)
    history = [
        *history,
        assistant.ChatMessage(role="user", text=message),
        assistant.ChatMessage(role="assistant", text=reply.text or reply.stopped or "(no answer)"),
    ]
    return assistant_ui.panel(history, True, inputs, reply)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "geometry_available": CAD_AVAILABLE}


# --- standards and rules --------------------------------------------------------------


@app.get("/api/standards")
def list_standards() -> dict[str, Any]:
    standards = _standards()
    return {
        "placeholder_tables": standards.placeholder_tables,
        "tables": {name: t.model_dump() for name, t in standards.tables.items()},
    }


@app.get("/api/rules")
def list_rules() -> list[dict[str, Any]]:
    return [rule.model_dump() for rule in RulesEngine.load().rules]


class CheckRequest(BaseModel):
    subjects: list[Subject]


class CheckResponse(BaseModel):
    results: list[RuleResult]
    blocking: bool = Field(
        description="True if any result is a fail: the design cannot be released"
    )


@app.post("/api/rules/check")
def check_rules(request: CheckRequest) -> CheckResponse:
    results = RulesEngine.load().check(request.subjects)
    return CheckResponse(results=results, blocking=has_blocking_failure(results))


# --- calculations ---------------------------------------------------------------------


class PressForcesRequest(BaseModel):
    contours: list[CuttingContour] = Field(min_length=1)
    thickness_mm: float = Field(gt=0)
    material: str


@app.post("/api/calc/press-forces")
def calc_press_forces(request: PressForcesRequest) -> PressForces:
    try:
        return press_forces(request.contours, request.thickness_mm, request.material, _standards())
    except StandardsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class StripLayoutRequest(BaseModel):
    outline: list[tuple[float, float]] = Field(
        min_length=3, description="Blank outline in mm, closed, first point not repeated"
    )
    thickness_mm: float = Field(gt=0)


@app.post("/api/calc/strip-layout")
def calc_strip_layout(request: StripLayoutRequest) -> StripLayoutOptions:
    try:
        return strip_layout_options(request.outline, request.thickness_mm, _standards())
    except StandardsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# --- geometry (needs CadQuery) --------------------------------------------------------


@app.post("/api/parts/recognise")
async def recognise_part(file: UploadFile) -> dict[str, Any]:
    """Upload a STEP file of a flat sheet-metal part; returns its recognised features."""
    _require_cad()
    from engine.recognition.sheet_metal import RecognitionError, load_part

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "part.step"
        path.write_bytes(await file.read())
        try:
            return load_part(path).model_dump()
        except (RecognitionError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


class ToolCheckRequest(BaseModel):
    features: list[dict[str, Any]] = Field(description="Feature tree, first item tool_spec")


@app.post("/api/tools/check")
def check_tool(request: ToolCheckRequest) -> CheckResponse:
    """Build a tool from its feature tree and run every geometry check and rule."""
    _require_cad()
    import json

    from engine.features.assembly import ToolAssembly

    try:
        tool = ToolAssembly.from_json(json.dumps(request.features))
        results = tool.build(_standards()).check(RulesEngine.load())
    except (ValueError, StandardsError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CheckResponse(results=results, blocking=has_blocking_failure(results))
