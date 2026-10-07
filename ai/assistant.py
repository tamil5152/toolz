"""The design assistant: Claude answers questions by calling the engine's tools.

Claude never produces a number itself. Every value comes from a tool (designer,
calculations, rules, standards), and the answer is checked afterwards: any number in
it that no tool returned and the user did not type is flagged in the UI.

The API key is read on the server from ANTHROPIC_API_KEY and never reaches the browser.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from ai.tools import TOOLS, Session, ToolCall, design_summary, run_tool
from rules.engine import RulesEngine
from rules.standards import Standards
from server.designer import DesignInput, DesignResult, evaluate

MODEL = "claude-opus-5-5"
MAX_STEPS = 15
MAX_TOKENS = 16000
# Refusal fallback: if the model declines, the API retries on Anthropic's recommended
# fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
API_KEY_ENV = "ANTHROPIC_API_KEY"
HISTORY_LIMIT = 12  # chat messages kept between turns

SYSTEM_PROMPT = """You are the design assistant in Tooldesign Studio, a press tool design \
application. You help a tool designer with the blanking and piercing tool on their screen.

Numbers: never calculate, estimate or recall a number yourself. Every value you state \
(forces, distances, pitches, utilisation, clearances, shut heights, limits) must come \
from a tool result in this conversation or from the user's design. If no tool gives a \
value, say so instead of guessing. Design changes you suggest must be evaluated with \
evaluate_design before you recommend them.

Rules: the shop rules are the authority. When a check passes, warns or fails, cite its \
rule id (for example PT-CLR-001) and its source. Tell the user when a value comes from \
a placeholder standards table, because those are not yet the shop's own numbers.

When you recommend changes, call propose_design once with the complete set; the user \
gets an Apply button. Finish with: what you propose (or that nothing needs to change), \
every warning and fail on the design you recommend with its rule id, and a short \
explanation. Write plain prose and short lists; units in mm, kN and %."""


class Client(Protocol):
    """The part of anthropic.Anthropic the assistant uses (client.beta.messages)."""

    @property
    def beta(self) -> Any: ...


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


@dataclass
class Reply:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    proposal: DesignInput | None = None
    proposal_reason: str = ""
    proposal_result: DesignResult | None = None
    unverified_numbers: list[str] = field(default_factory=list)
    stopped: str = ""  # why the answer is incomplete, if it is
    error: str = ""  # the request failed; text is empty


def configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def default_client() -> Client:
    import anthropic

    client: Client = anthropic.Anthropic(timeout=240.0, max_retries=1)
    return client


def ask(
    question: str,
    design: DesignInput,
    history: list[ChatMessage],
    standards: Standards,
    rules: RulesEngine,
    client: Client | None = None,
) -> Reply:
    """Answer one question about the design, calling tools for up to MAX_STEPS steps."""
    session = Session(design=design, standards=standards, rules=rules)
    current = design_summary(evaluate(design, standards, rules))
    context = (
        "The user's current design (inputs):\n"
        + json.dumps(design.model_dump(), separators=(",", ":"))
        + "\n\nIts current results:\n"
        + json.dumps(current, separators=(",", ":"))
    )
    messages: list[dict[str, Any]] = [
        {"role": m.role, "content": m.text} for m in history[-HISTORY_LIMIT:]
    ]
    messages.append({"role": "user", "content": f"{context}\n\nQuestion: {question}"})

    try:
        client = client or default_client()
        text, stopped = _loop(client, messages, session)
    except Exception as error:  # noqa: BLE001 - every API failure is shown in the chat
        return Reply(text="", calls=session.calls, error=_api_error_message(error))

    sources = [question, context, *(m.text for m in history)]
    sources += [json.dumps(c.input) + json.dumps(c.output) for c in session.calls]
    return Reply(
        text=text,
        calls=session.calls,
        proposal=session.proposal,
        proposal_reason=session.proposal_reason,
        proposal_result=session.proposal_result,
        unverified_numbers=unverified_numbers(text, sources),
        stopped=stopped,
    )


def _loop(client: Client, messages: list[dict[str, Any]], session: Session) -> tuple[str, str]:
    tools = [t.definition() for t in TOOLS.values()]
    texts: list[str] = []
    for _ in range(MAX_STEPS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
            output_config={"effort": "medium"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return "", "The model declined to answer this request."
        texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
        if response.stop_reason == "max_tokens":
            return "\n\n".join(texts), "The answer was cut off at the length limit."
        if response.stop_reason != "tool_use":
            return "\n\n".join(texts), ""

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            call = run_tool(session, block.name, block.input)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(call.output, separators=(",", ":")),
                    "is_error": call.is_error,
                }
            )
        messages.append({"role": "user", "content": results})
    return "\n\n".join(texts), f"Stopped after {MAX_STEPS} steps before finishing."


def _api_error_message(error: Exception) -> str:
    import anthropic

    if isinstance(error, anthropic.AuthenticationError):
        return f"The API key in {API_KEY_ENV} was rejected. Check it in the server settings."
    if isinstance(error, anthropic.PermissionDeniedError):
        return "The API key does not have access to this model."
    if isinstance(error, anthropic.RateLimitError):
        return "The assistant is busy (rate limited). Try again in a moment."
    if isinstance(error, anthropic.APITimeoutError):
        return "The assistant took too long to answer. Try a narrower question."
    if isinstance(error, anthropic.APIConnectionError):
        return "Could not reach the Claude API from the server."
    if isinstance(error, anthropic.APIStatusError):
        return f"The Claude API returned an error ({error.status_code})."
    return f"The assistant failed: {type(error).__name__}."


# --- number check ---------------------------------------------------------------------

# A number not glued to a letter, hyphen or dot in front: skips ids like PT-CLR-001 and H2.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.\-])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9_.,\-])-?\d+(?:\.\d+)?"
)
_SMALL_INTEGER = 10  # counts and list numbers ("2 holes", "step 3") are not checked


def unverified_numbers(text: str, sources: list[str]) -> list[str]:
    """Numbers in ``text`` that do not appear, to the precision written, in any source."""
    known = {abs(float(m.replace(",", ""))) for s in sources for m in _NUMBER.findall(s)}
    flagged: list[str] = []
    for token in _NUMBER.findall(text):
        value = abs(float(token.replace(",", "")))
        if value <= _SMALL_INTEGER and value == int(value):
            continue
        decimals = len(token.split(".")[1]) if "." in token else 0
        tolerance = 0.5 * 10**-decimals + 1e-9
        if not any(abs(k - value) <= tolerance for k in known) and token not in flagged:
            flagged.append(token)
    return flagged
