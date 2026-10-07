# Tooldesign

AI-assisted press tool design on OpenCASCADE. A Python engine builds every tool
feature from parameters and checks it against shop rules; Claude proposes design
steps by calling those functions, and the designer accepts, edits or rejects each one.

See `CLAUDE.md` for the hard rules and layout.

## Setup

```
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,cad]"
pytest -q
```

## Web API

```
uvicorn server.app:app --reload
```

Open http://localhost:8000 for the landing page and http://localhost:8000/docs for the
interactive API. On Vercel, the same app is deployed from `server/app.py` with only
the light dependencies; part import and tool building need the `cad` extra and answer
503 there.
