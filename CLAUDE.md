# Tooldesign: AI-assisted press tool design

## What this is
A Python tool-design engine on OpenCASCADE (via CadQuery and OCP), with an AI
assistant (Claude via the Anthropic Python SDK) that designs press tools together
with a human designer. First target: simple progressive press tools for flat
sheet-metal parts. First UI: a FreeCAD 1.x workbench.

## Hard rules
- Python 3.12 only. No TypeScript or JavaScript anywhere. The web UI (server/ui.py)
  is HTML generated in Python; live updates use htmx from a CDN through HTML
  attributes only, so no JavaScript is written in this repository.
- The LLM never produces numbers or geometry directly. Every value comes from a
  calculation function, a rule table in standards/, or the designer.
- Every feature is a pure, parametric function: inputs (Pydantic model with units
  in field names, e.g. thickness_mm) -> solids + metadata. No hidden state.
- Every new feature, rule or calculation ships with pytest tests first.
- Never invent tool-design values (clearances, minimum sizes, standard part
  dimensions). If a value is not in standards/, stop and ask me.
- All units are millimetres, newtons, kilonewtons and degrees, stated in names.
- Geometry must pass OpenCASCADE validity checks (BRepCheck_Analyzer) in tests.
- Rules live in YAML in standards/, evaluated by a safe evaluator. Never use eval().

## Layout
engine/ (features, recognition, calc, export) - rules/ - ai/ - knowledge/ -
server/ (FastAPI, Celery) - data/ (SQLAlchemy, Alembic) - clients/freecad_wb/ -
clients/desktop/ (PySide6, later) - standards/ - tests/ (golden_parts/)

## Geometry library
CadQuery only. build123d is not installed alongside it: the two pull different
OCP wheels (with and without VTK) that overwrite each other and break imports.

## Deployment
The web API (server/app.py, FastAPI) is deployed on Vercel. Vercel installs only the
base dependencies; CadQuery is the optional `cad` extra because it is over the 500 MB
function limit. Never import cadquery or scipy at module level from rules/,
engine/calc/ or server/: geometry endpoints import it lazily and answer 503 without it.

## Placeholder standards
Values in standards/ marked `placeholder: true` are NOT shop values. Tests marked
`needs_shop_standards` fail until they are replaced; CI deselects that marker
until the real tables are in. Never treat a placeholder as a real value.

## Commands
- Install: pip install -e ".[dev,cad]"
- Tests: pytest -q
- Lint and format: ruff check . && ruff format .
- Types: mypy engine rules ai server

## Working style
- Small steps. After each step: run tests, show me what changed, and stop.
- Prefer clear code over clever code; a tool designer must be able to read the
  rules and calculation functions.
- When unsure about tool-design practice, ask instead of guessing.
