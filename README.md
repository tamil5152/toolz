# Tooldesign

AI-assisted press tool design on OpenCASCADE. A Python engine builds every tool
feature from parameters and checks it against shop rules; Claude proposes design
steps by calling those functions, and the designer accepts, edits or rejects each one.

See `CLAUDE.md` for the hard rules and layout.

## Setup

```
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```
