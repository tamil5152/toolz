"""The design assistant's chat panel, next to the live results.

htmx attributes only: the chat form posts the question together with the design form
(hx-include) to /ui/assistant, which returns this whole panel. The conversation so far
travels in a hidden field as plain text, so the server keeps no state.
"""

from __future__ import annotations

import json
import re
from html import escape

from pydantic import TypeAdapter, ValidationError

from ai.assistant import API_KEY_ENV, HISTORY_LIMIT, ChatMessage, Reply
from server.designer import DesignInput

_HISTORY = TypeAdapter(list[ChatMessage])
PLACEHOLDER = "e.g. Will this run on the PR-400? What would make every rule pass?"


def parse_history(raw: str) -> list[ChatMessage]:
    """The conversation from the hidden field; anything unreadable starts a new one."""
    try:
        return _HISTORY.validate_json(raw)[-HISTORY_LIMIT:] if raw else []
    except ValidationError:
        return []


def history_json(history: list[ChatMessage]) -> str:
    return json.dumps([m.model_dump() for m in history[-HISTORY_LIMIT:]])


def panel(
    history: list[ChatMessage],
    configured: bool,
    design: DesignInput,
    reply: Reply | None = None,
    message: str = "",
) -> str:
    log: list[str] = []
    if not history and reply is None:
        log.append(
            '<p class="muted">Ask about the design on screen. Answers come from the same '
            "calculations and shop rules as the results, never from the model's own "
            "arithmetic.</p>"
        )
    for i, m in enumerate(history):
        if m.role == "user":
            log.append(f'<div class="msg user">{escape(m.text)}</div>')
        else:
            extra = _reply_details(reply, design, history) if i == len(history) - 1 else ""
            log.append(f'<div class="msg bot">{prose(m.text)}{extra}</div>')
    if reply is not None and reply.error:
        log.append(f'<div class="msg bot error">{escape(reply.error)}</div>')

    if configured:
        form = f"""
<form class="ask" hx-post="/ui/assistant" hx-include="#design" hx-target="#assistant"
      hx-swap="outerHTML" hx-indicator="#assistant" hx-disabled-elt="find button">
  <input type="hidden" name="history" value="{escape(history_json(history))}">
  <textarea name="message" rows="3" required placeholder="{escape(PLACEHOLDER)}">{escape(message)}</textarea>
  <div class="ask-row"><span class="working">Working through the calculations…</span>
    <button type="submit">Ask</button></div>
</form>"""
    else:
        form = (
            '<p class="notice">The assistant is not set up on this server yet. Add an Anthropic '
            f"API key as the <code>{API_KEY_ENV}</code> environment variable in the hosting "
            "settings and redeploy. The key stays on the server; it is never sent to the "
            "browser.</p>"
        )
    return (
        '<section id="assistant" class="assistant card" aria-live="polite">'
        f'<h2>Design assistant</h2><div class="chat-log">{"".join(log)}</div>{form}</section>'
    )


def _reply_details(reply: Reply | None, design: DesignInput, history: list[ChatMessage]) -> str:
    if reply is None:
        return ""
    parts: list[str] = []
    if reply.stopped:
        parts.append(f'<p class="flag">{escape(reply.stopped)}</p>')
    if reply.unverified_numbers:
        parts.append(
            '<p class="flag">Not from a calculation, check before relying on: '
            f"{escape(', '.join(reply.unverified_numbers))}</p>"
        )
    if reply.proposal is not None:
        parts.append(_proposal(reply, design, history))
    if reply.calls:
        items = "".join(
            f'<li class="{"err" if c.is_error else ""}"><code>{escape(c.name)}</code> '
            f"{escape(_clip(json.dumps(c.input)))}"
            + (f" <em>{escape(_clip(str(c.output.get('error', ''))))}</em>" if c.is_error else "")
            + "</li>"
            for c in reply.calls
        )
        parts.append(
            f"<details><summary>{len(reply.calls)} calculation"
            f"{'s' if len(reply.calls) != 1 else ''} run</summary><ul>{items}</ul></details>"
        )
    return "".join(parts)


def _proposal(reply: Reply, design: DesignInput, history: list[ChatMessage]) -> str:
    assert reply.proposal is not None
    old, new = design.model_dump(), reply.proposal.model_dump()
    rows = "".join(
        f"<tr><td>{escape(_label(k))}</td><td>{escape(_value(old[k]))}</td>"
        f"<td><b>{escape(_value(v))}</b></td></tr>"
        for k, v in new.items()
        if v != old[k]
    )
    result = reply.proposal_result
    checks = ""
    if result is not None:
        flagged = [r for r in result.rules if r.status != "pass"]
        items = [
            f'<li><span class="pill {r.status}">{r.status}</span> {escape(r.rule_id)} '
            f"{escape(r.subject_id)}: {escape(r.message)}</li>"
            for r in flagged
        ] + [f'<li><span class="pill fail">input</span> {escape(e)}</li>' for e in result.errors]
        checks = (
            f'<ul class="checks">{"".join(items)}</ul>'
            if items
            else f'<p class="ok">All {len(result.rules)} rule checks pass on this proposal.</p>'
        )
    hidden = "".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(_value(v))}">'
        for k, v in new.items()
    )
    return f"""<div class="proposal"><h3>Proposed changes</h3>
<p class="muted">{escape(reply.proposal_reason)}</p>
<table><thead><tr><th>Input</th><th>Now</th><th>Proposed</th></tr></thead><tbody>{rows}</tbody></table>
{checks}
<form method="post" action="/">{hidden}
  <input type="hidden" name="history" value="{escape(history_json(history))}">
  <button type="submit">Apply to design</button></form></div>"""


def _label(field: str) -> str:
    unit = " (mm)" if field.endswith("_mm") else ""
    return field.removesuffix("_mm").replace("_", " ").capitalize() + unit


def _value(value: object) -> str:
    return format(value, ".10g") if isinstance(value, float) else str(value)


def _clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- text -----------------------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def prose(text: str) -> str:
    """Claude's answer as safe HTML: paragraphs, lists, **bold** and `code`; nothing else."""
    blocks: list[str] = []
    items: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif _BULLET.match(line):
            if paragraph:
                flush()
            items.append(_BULLET.sub("", line, count=1))
        elif stripped.startswith("#"):
            flush()
            blocks.append(f"<p><strong>{_inline(stripped.lstrip('#').strip())}</strong></p>")
        else:
            if items:
                flush()
            paragraph.append(stripped)
    flush()
    return "".join(blocks)


def _inline(text: str) -> str:
    html = escape(text)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
