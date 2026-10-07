"""The designer's web page: inputs on the left, results recalculated live on the right.

Pages are HTML generated in Python. Live updates use htmx (loaded from a CDN) through
HTML attributes only: each input change posts the form to /ui/update, which returns
the results panel. Without htmx the form still works with a normal submit.
"""

from __future__ import annotations

from html import escape

from rules.engine import RuleResult
from rules.standards import Standards
from server.designer import DesignInput, DesignResult
from server.drawings import blank_svg, stack_elevation_svg, stack_iso_svg, strip_svg

HTMX_URL = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"
HTMX_SRI = "sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+"


def _f(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def _options(values: list[str], selected: str) -> str:
    return "".join(
        f'<option value="{escape(v)}"{" selected" if v == selected else ""}>{escape(v.replace("_", " "))}</option>'
        for v in values
    )


def _number(
    name: str, label: str, value: float, unit: str = "mm", step: str = "any", cls: str = ""
) -> str:
    return (
        f'<label class="field {cls}"><span>{escape(label)}</span>'
        f'<span class="input-unit"><input type="number" name="{name}" value="{value:g}" step="{step}" min="0">'
        f"<em>{escape(unit)}</em></span></label>"
    )


# --- page -----------------------------------------------------------------------------


def page(
    inputs: DesignInput, result: DesignResult, standards: Standards, cad_available: bool
) -> str:
    materials = [str(r["material"]) for r in standards.table("materials").rows]
    presses = [str(r["press_id"]) for r in standards.table("presses").rows]
    die_sets = [str(r["die_set_id"]) for r in standards.table("die_sets").rows]
    shapes = ["rectangle", "circle", "custom"]
    form = f"""
<form id="design" method="post" action="/" hx-post="/ui/update" hx-target="#results"
      hx-trigger="input changed delay:250ms, change" hx-indicator="#live">
  <fieldset>
    <legend>Part</legend>
    <label class="field"><span>Blank shape</span>
      <select name="shape" id="shape">{_options(shapes, inputs.shape)}</select></label>
    <div class="grid2 only-rectangle">
      {_number("length_mm", "Length", inputs.length_mm)}
      {_number("width_mm", "Width", inputs.width_mm)}
      {_number("corner_radius_mm", "Corner radius", inputs.corner_radius_mm)}
    </div>
    <div class="only-circle">{_number("diameter_mm", "Diameter", inputs.diameter_mm)}</div>
    <label class="field only-custom"><span>Outline points (x, y per line, mm)</span>
      <textarea name="custom_points" rows="6">{escape(inputs.custom_points)}</textarea></label>
    <label class="field"><span>Holes (diameter, x, y per line, mm)</span>
      <textarea name="holes" rows="4">{escape(inputs.holes)}</textarea></label>
  </fieldset>
  <fieldset>
    <legend>Material and press</legend>
    <label class="field"><span>Material</span>
      <select name="material">{_options(materials, inputs.material)}</select></label>
    {_number("thickness_mm", "Sheet thickness", inputs.thickness_mm)}
    <label class="field"><span>Press</span>
      <select name="press_id">{_options(presses, inputs.press_id)}</select></label>
  </fieldset>
  <fieldset>
    <legend>Tool stack</legend>
    <label class="field"><span>Die set</span>
      <select name="die_set_id">{_options(die_sets, inputs.die_set_id)}</select></label>
    <div class="grid2">
      {_number("plate_length_mm", "Plate length", inputs.plate_length_mm)}
      {_number("plate_width_mm", "Plate width", inputs.plate_width_mm)}
      {_number("die_plate_thickness_mm", "Die plate", inputs.die_plate_thickness_mm)}
      {_number("stripper_gap_mm", "Stripper gap", inputs.stripper_gap_mm)}
      {_number("stripper_thickness_mm", "Stripper", inputs.stripper_thickness_mm)}
      {_number("punch_holder_thickness_mm", "Punch holder", inputs.punch_holder_thickness_mm)}
      {_number("back_plate_thickness_mm", "Back plate", inputs.back_plate_thickness_mm)}
      {_number("punch_length_mm", "Punch length", inputs.punch_length_mm)}
      {_number("die_penetration_mm", "Die penetration", inputs.die_penetration_mm)}
    </div>
  </fieldset>
  <noscript><button type="submit">Recalculate</button></noscript>
</form>"""

    geometry = (
        "3D solid modelling and STEP import are available on this server."
        if cad_available
        else "3D solid modelling and STEP import need the CAD engine, which is too large for "
        "this hosting. Everything on this page is calculated live without it; run the engine "
        "locally for solid models and STEP files."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tooldesign Studio</title>
<script src="{HTMX_URL}" integrity="{HTMX_SRI}" crossorigin="anonymous" defer></script>
<style>{CSS}</style></head>
<body>
<header class="top">
  <div><strong>Tooldesign Studio</strong><span class="sub">Press tool design, calculated live</span></div>
  <div class="live" id="live"><span class="dot"></span>updating</div>
</header>
<main class="layout">
  <aside class="inputs">{form}
    <p class="note">{escape(geometry)}</p>
    <p class="note"><a href="/docs">API documentation</a></p>
  </aside>
  <section id="results" class="results" aria-live="polite">{results(result)}</section>
</main>
</body></html>"""


# --- results panel --------------------------------------------------------------------


def results(r: DesignResult) -> str:
    parts = [_banner(r)]
    if r.errors:
        items = "".join(f"<li>{escape(e)}</li>" for e in r.errors)
        parts.append(f'<div class="card error"><h2>Fix these inputs</h2><ul>{items}</ul></div>')
    parts.append(_tiles(r))
    if r.outline and r.layouts:
        best = r.layouts.best
        parts.append(
            f"""<div class="row">
  <div class="card"><h2>Blank</h2>{blank_svg(r.outline, r.holes)}
    <p class="muted">Blank area {_f(r.blank_area_mm2)} mm² after holes</p></div>
  <div class="card wide"><h2>Strip layout, best rotation {best.angle_deg:g}°</h2>{strip_svg(r.outline, r.holes, best)}
    {_layout_table(r)}</div>
</div>"""
        )
    if r.stack is not None:
        parts.append(
            f"""<div class="row">
  <div class="card"><h2>Tool stack, closed</h2>{stack_elevation_svg(r.stack, r.inputs, r.die_set)}</div>
  <div class="card"><h2>3D view</h2>{stack_iso_svg(r.stack, r.inputs, r.die_set)}
    <p class="muted">Plates and shoes, spaced apart to show each layer.</p></div>
</div>"""
        )
    parts.append(_rules_table(r.rules))
    return "".join(parts)


def _banner(r: DesignResult) -> str:
    fails = sum(1 for x in r.rules if x.status == "fail")
    warnings = sum(1 for x in r.rules if x.status == "warning")
    if r.errors or fails:
        cls, text = (
            "bad",
            f"Not releasable: {fails} rule failures"
            + (f", {len(r.errors)} input problems" if r.errors else ""),
        )
    elif warnings:
        cls, text = "warn", f"All rules pass with {warnings} warnings"
    else:
        cls, text = "good", "All rules pass"
    placeholder = ""
    if r.placeholder_tables:
        placeholder = (
            '<p class="ph">Results use <strong>placeholder standards</strong> '
            f"({escape(', '.join(r.placeholder_tables))}). Replace them with your shop's values "
            "before trusting any number.</p>"
        )
    return f'<div class="banner {cls}"><strong>{escape(text)}</strong>{placeholder}</div>'


def _tile(label: str, value: str, note: str = "", cls: str = "") -> str:
    return (
        f'<div class="tile {cls}"><span>{escape(label)}</span><b>{escape(value)}</b>'
        f"<small>{escape(note)}</small></div>"
    )


def _tiles(r: DesignResult) -> str:
    tiles = []
    capacity = next(
        (x.measured.get("capacity_kn") for x in r.rules if x.rule_id == "PT-TON-001"), None
    )
    if r.forces is not None:
        status = next((x.status for x in r.rules if x.rule_id == "PT-TON-001"), "")
        note = f"press capacity {capacity:g} kN" if isinstance(capacity, int | float) else ""
        tiles.append(_tile("Press force", f"{_f(r.forces.press_force_kn)} kN", note, status))
        tiles.append(
            _tile(
                "Cutting force",
                f"{_f(r.forces.cutting_force_n / 1000)} kN",
                f"+ stripping {_f(r.forces.stripping_force_n / 1000)} kN",
            )
        )
        tiles.append(
            _tile(
                "Centre of pressure",
                f"{_f(r.forces.centre_of_pressure_x_mm)}, {_f(r.forces.centre_of_pressure_y_mm)}",
                "mm from blank corner",
            )
        )
    if r.layouts is not None:
        tiles.append(
            _tile(
                "Material use",
                f"{r.layouts.best.utilisation * 100:.1f} %",
                f"at {r.layouts.best.angle_deg:g}°, pitch {_f(r.layouts.best.pitch_mm, 2)} mm",
            )
        )
    if r.stack is not None:
        status = next((x.status for x in r.rules if x.rule_id == "PT-SH-001"), "")
        tiles.append(
            _tile("Shut height", f"{_f(r.stack.shut_height_mm)} mm", "closed tool", status)
        )
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _layout_table(r: DesignResult) -> str:
    assert r.layouts is not None
    rows = [("Best", r.layouts.best), ("0°", r.layouts.at_0_deg), ("180°", r.layouts.at_180_deg)]
    body = "".join(
        f"<tr><td>{name}</td><td>{x.angle_deg:g}°</td><td>{_f(x.pitch_mm, 2)}</td>"
        f"<td>{_f(x.strip_width_mm, 2)}</td><td>{x.utilisation * 100:.1f} %</td></tr>"
        for name, x in rows
    )
    return (
        "<table><thead><tr><th>Layout</th><th>Rotation</th><th>Pitch (mm)</th>"
        f"<th>Strip width (mm)</th><th>Material use</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _rules_table(rules: list[RuleResult]) -> str:
    order = {"fail": 0, "warning": 1, "pass": 2}
    rows = "".join(
        f'<tr><td><span class="pill {x.status}">{x.status}</span></td><td>{escape(x.rule_id)}</td>'
        f"<td>{escape(x.subject_id)}</td><td>{escape(x.message)}</td>"
        f'<td class="muted">{escape(", ".join(f"{k} = {_short(v)}" for k, v in x.measured.items()))}</td></tr>'
        for x in sorted(rules, key=lambda x: (order[x.status], x.rule_id, x.subject_id))
    )
    return (
        '<div class="card"><h2>Rule checks</h2><div class="scroll"><table><thead><tr><th>Result</th>'
        "<th>Rule</th><th>Item</th><th>Check</th><th>Values</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
    )


def _short(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3g}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


CSS = """
:root { --bg:#f6f7f9; --panel:#ffffff; --ink:#1d232b; --muted:#5f6b7a; --line:#d9dee5;
  --accent:#2563eb; --good:#15803d; --good-bg:#e8f5ec; --warn:#b45309; --warn-bg:#fdf3e2;
  --bad:#b91c1c; --bad-bg:#fdecec; --part:#c7d7f5; --part-line:#2f5fb8; --hole:#ffffff;
  --strip:#eef1f5; --shoe:#9aa7b8; --plate:#b9c6d6; --die:#7f95b3; --stripper:#d5b98a;
  --punch:#e2574c; --sheet:#3d7f5d; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f1318; --panel:#171c23;
  --ink:#e6eaf0; --muted:#97a3b3; --line:#2a323d; --accent:#6ea0ff; --good:#4ade80; --good-bg:#12271b;
  --warn:#fbbf24; --warn-bg:#2b2112; --bad:#f87171; --bad-bg:#2c1416; --part:#29406b; --part-line:#86a9f0;
  --hole:#171c23; --strip:#1f262f; --shoe:#5b6676; --plate:#6f7f94; --die:#4d6585; --stripper:#8a7350;
  --punch:#e2574c; --sheet:#4caf80; } }
:root[data-theme="dark"] { --bg:#0f1318; --panel:#171c23; --ink:#e6eaf0; --muted:#97a3b3; --line:#2a323d; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }
.top { display:flex; justify-content:space-between; align-items:center; padding:12px 20px;
  background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:2; }
.top .sub { color:var(--muted); margin-left:10px; }
.live { color:var(--muted); font-size:12px; opacity:0; transition:opacity .15s; }
.live.htmx-request { opacity:1; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--accent); margin-right:6px; }
.layout { display:grid; grid-template-columns:340px 1fr; gap:16px; padding:16px; max-width:1500px; margin:0 auto; }
@media (max-width: 900px) { .layout { grid-template-columns:1fr; padding:16px; } }
.inputs fieldset { border:1px solid var(--line); background:var(--panel); border-radius:10px; margin:0 0 12px; padding:10px 12px 12px; }
legend { font-weight:600; padding:0 4px; }
.field { display:flex; flex-direction:column; gap:4px; margin-top:8px; min-width:0; }
.field > span:first-child { color:var(--muted); font-size:12px; }
input, select, textarea { width:100%; font:inherit; color:var(--ink); background:var(--bg); border:1px solid var(--line);
  border-radius:6px; padding:6px 8px; }
textarea { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; resize:vertical; }
input:focus, select:focus, textarea:focus { outline:2px solid var(--accent); outline-offset:-1px; }
.input-unit { display:flex; align-items:center; gap:6px; }
.input-unit em { font-style:normal; color:var(--muted); font-size:12px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:0 10px; }
form:has(#shape option[value="rectangle"]:checked) .only-circle,
form:has(#shape option[value="rectangle"]:checked) .only-custom,
form:has(#shape option[value="circle"]:checked) .only-rectangle,
form:has(#shape option[value="circle"]:checked) .only-custom,
form:has(#shape option[value="custom"]:checked) .only-rectangle,
form:has(#shape option[value="custom"]:checked) .only-circle { display:none; }
.note { color:var(--muted); font-size:12px; }
.note a { color:var(--accent); }
.results { display:flex; flex-direction:column; gap:12px; min-width:0; }
.banner { border-radius:10px; padding:10px 14px; border:1px solid var(--line); }
.banner.good { background:var(--good-bg); color:var(--good); }
.banner.warn { background:var(--warn-bg); color:var(--warn); }
.banner.bad { background:var(--bad-bg); color:var(--bad); }
.banner .ph { margin:6px 0 0; color:var(--ink); font-size:13px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }
.tile { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 12px; display:flex; flex-direction:column; }
.tile span { color:var(--muted); font-size:12px; }
.tile b { font-size:22px; font-variant-numeric:tabular-nums; }
.tile small { color:var(--muted); }
.tile.fail { border-color:var(--bad); } .tile.fail b { color:var(--bad); }
.tile.pass b { color:var(--good); }
.row { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,2fr); gap:12px; }
.row:last-of-type { grid-template-columns:minmax(0,1fr) minmax(0,1fr); }
@media (max-width: 1100px) { .row, .row:last-of-type { grid-template-columns:1fr; } }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; min-width:0; }
.card h2 { font-size:14px; margin:0 0 8px; }
.card.error { border-color:var(--bad); } .card.error h2 { color:var(--bad); }
.muted { color:var(--muted); font-size:12px; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:500; }
td:nth-child(-n+3) { white-space:nowrap; }
.pill { display:inline-block; border-radius:999px; padding:1px 8px; font-size:12px; font-weight:600; }
.pill.pass { background:var(--good-bg); color:var(--good); }
.pill.warning { background:var(--warn-bg); color:var(--warn); }
.pill.fail { background:var(--bad-bg); color:var(--bad); }
svg.drawing { width:100%; height:auto; max-height:380px; display:block; margin:0 auto; }
.drawing .part { fill:var(--part); stroke:var(--part-line); stroke-width:1.2; }
.drawing .hole { fill:var(--hole); stroke:var(--part-line); stroke-width:1; }
.drawing .strip { fill:var(--strip); stroke:var(--line); }
.drawing .dim { stroke:var(--muted); stroke-width:1; }
.drawing .lbl { fill:var(--ink); font-size:12px; }
.drawing .small { font-size:10px; fill:var(--muted); }
.drawing .shoe { fill:var(--shoe); } .drawing .plate { fill:var(--plate); }
.drawing .die { fill:var(--die); } .drawing .stripper { fill:var(--stripper); }
.drawing rect.shoe, .drawing rect.plate, .drawing rect.die, .drawing rect.stripper { stroke:var(--panel); stroke-width:1; }
.drawing .punch { fill:var(--punch); } .drawing .sheet { fill:var(--sheet); }
.drawing .iso-top { filter:brightness(1.12); stroke:var(--panel); stroke-width:.8; }
.drawing .iso-front { stroke:var(--panel); stroke-width:.8; }
.drawing .iso-side { filter:brightness(.82); stroke:var(--panel); stroke-width:.8; }
"""
