"""The web API that Vercel serves."""

import math
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.app
from server.app import app

client = TestClient(app)


def test_home_page_is_the_live_designer() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'hx-post="/ui/update"' in response.text
    assert "Strip layout" in response.text
    assert "placeholder" in response.text  # shipped standards are still placeholders


def test_live_update_returns_the_results_panel() -> None:
    form = {"shape": "circle", "diameter_mm": "40", "holes": "10, 20, 20", "thickness_mm": "2"}
    response = client.post("/ui/update", data=form)
    assert response.status_code == 200
    assert "<html" not in response.text  # a fragment, swapped into the page
    assert "Rule checks" in response.text


def test_plain_form_submit_returns_the_full_page() -> None:
    response = client.post("/", data={"length_mm": "90"})
    assert response.status_code == 200
    assert 'name="length_mm" value="90"' in response.text


def test_health() -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_standards_and_rules_are_listed() -> None:
    standards = client.get("/api/standards").json()
    assert "materials" in standards["tables"]
    rules = client.get("/api/rules").json()
    assert any(r["id"] == "PT-CLR-001" for r in rules)


def test_rules_check() -> None:
    subject = {
        "kind": "pierce_punch",
        "id": "P1",
        "values": {"material": "mild_steel", "thickness_mm": 2, "diameter_mm": 0.5},
    }
    body = client.post("/api/rules/check", json={"subjects": [subject]}).json()
    statuses = {r["rule_id"]: r["status"] for r in body["results"]}
    assert statuses["PT-MIN-004"] == "warning"


def test_press_forces() -> None:
    contour = {"id": "H1", "length_mm": math.pi * 10, "centroid_x_mm": 0, "centroid_y_mm": 0}
    response = client.post(
        "/api/calc/press-forces",
        json={"contours": [contour], "thickness_mm": 2, "material": "mild_steel"},
    )
    assert response.status_code == 200
    assert response.json()["cutting_force_n"] > 0


def test_unknown_material_is_a_422() -> None:
    contour = {"id": "H1", "length_mm": 10, "centroid_x_mm": 0, "centroid_y_mm": 0}
    response = client.post(
        "/api/calc/press-forces",
        json={"contours": [contour], "thickness_mm": 2, "material": "unobtainium"},
    )
    assert response.status_code == 422


def test_strip_layout() -> None:
    outline = [[0, 0], [50, 0], [50, 30], [0, 30]]
    response = client.post("/api/calc/strip-layout", json={"outline": outline, "thickness_mm": 1.5})
    assert response.status_code == 200
    assert response.json()["best"]["utilisation"] > 0


def test_geometry_endpoints_answer_503_without_cadquery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.app, "CAD_AVAILABLE", False)
    assert client.post("/api/tools/check", json={"features": []}).status_code == 503


def test_part_recognition_upload(tmp_path: Path) -> None:
    cq = pytest.importorskip("cadquery")
    from engine.export.step import export_step

    part = cq.Workplane("XY").box(100, 60, 2).faces(">Z").workplane().hole(10)
    step = export_step(part.val(), tmp_path / "part.step")
    response = client.post("/api/parts/recognise", files={"file": ("part.step", step.read_bytes())})
    assert response.status_code == 200
    assert [f["kind"] for f in response.json()["features"]] == ["round_hole"]


def test_server_does_not_import_cadquery_at_module_level() -> None:
    """The Vercel deployment has no CadQuery or SciPy; importing the app must not need them."""
    code = (
        "import sys; sys.modules['cadquery'] = None; sys.modules['scipy'] = None\n"
        "import server.app\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_tool_check_runs_the_assembly_checks() -> None:
    pytest.importorskip("cadquery")
    import json

    from tests.test_assembly import simple_tool

    features = json.loads(simple_tool().to_json())
    response = client.post("/api/tools/check", json={"features": features})
    assert response.status_code == 200
    rule_ids = {r["rule_id"] for r in response.json()["results"]}
    assert {"GEO-ALIGN", "PT-SH-001", "PT-FAS-001"} <= rule_ids
