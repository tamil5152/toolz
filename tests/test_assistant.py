"""The design assistant's tool loop, with the Claude API replaced by a scripted fake.

No network: each test scripts the model's responses and checks what the assistant
sends back, what it records, and what the chat panel shows.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import ai.assistant
from ai.assistant import MAX_STEPS, MODEL, ChatMessage, Reply, ask, unverified_numbers
from ai.tools import TOOLS, Session, run_tool
from rules.engine import RulesEngine
from rules.standards import Standards
from server.app import app
from server.assistant_ui import panel, parse_history, prose
from server.designer import DesignInput

FIXTURES = Path(__file__).parent / "fixtures" / "standards"
STANDARDS = Standards.load(FIXTURES)
RULES = RulesEngine.load(FIXTURES)


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(id: str, name: str, input: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def response(stop_reason: str, *content: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


class FakeClient:
    """Stands in for anthropic.Anthropic: returns scripted responses, records requests."""

    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> SimpleNamespace:
        # Messages are appended to in place; keep a copy as sent.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def ask_with(client: FakeClient, question: str = "Will this run?") -> Reply:
    return ask(question, DesignInput(), [], STANDARDS, RULES, client=client)


def test_tools_run_and_results_go_back_in_one_message() -> None:
    client = FakeClient(
        response(
            "tool_use",
            text("Checking two options."),
            tool_use("t1", "evaluate_design", {"changes": {"punch_length_mm": 85}}),
            tool_use("t2", "lookup_standard", {"table": "presses", "key": {"press_id": "PR-400"}}),
        ),
        response("end_turn", text("Done.")),
    )
    reply = ask_with(client)

    assert reply.text == "Done."
    assert [c.name for c in reply.calls] == ["evaluate_design", "lookup_standard"]
    assert not any(c.is_error for c in reply.calls)
    sent = client.requests[1]["messages"]
    assert sent[-2]["role"] == "assistant"
    results = sent[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    evaluated = json.loads(results[0]["content"])
    assert evaluated["shut_height_mm"] > 0
    assert {r["rule_id"] for r in evaluated["rules"]} >= {"PT-TON-001", "PT-SH-001"}


def test_request_shape() -> None:
    client = FakeClient(response("end_turn", text("Hello.")))
    ask_with(client, "What is the press force?")
    request = client.requests[0]
    assert request["model"] == MODEL
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert "tool_choice" not in request  # forcing a tool call is rejected on this model
    assert {t["name"] for t in request["tools"]} == set(TOOLS)
    first = request["messages"][0]["content"]
    assert "current design" in first and "press_force_kn" in first
    assert first.endswith("Question: What is the press force?")
    assert "never calculate" in request["system"]


def test_proposal_is_recorded_and_checked_without_changing_the_design() -> None:
    client = FakeClient(
        response(
            "tool_use",
            tool_use(
                "t1",
                "propose_design",
                {"changes": {"punch_length_mm": 70}, "reason": "Shorter punch, see PT-SH-001."},
            ),
        ),
        response("end_turn", text("Proposed a shorter punch.")),
    )
    design = DesignInput()
    reply = ask("Shorten it", design, [], STANDARDS, RULES, client=client)
    assert reply.proposal is not None and reply.proposal.punch_length_mm == 70
    assert reply.proposal_result is not None and reply.proposal_result.rules
    assert design.punch_length_mm == 80  # the user's design is untouched


def test_bad_tool_input_goes_back_as_an_error() -> None:
    client = FakeClient(
        response("tool_use", tool_use("t1", "evaluate_design", {"changes": {"thickness_mm": -1}})),
        response("tool_use", tool_use("t2", "no_such_tool", {})),
        response("end_turn", text("Sorry.")),
    )
    reply = ask_with(client)
    assert [c.is_error for c in reply.calls] == [True, True]
    first_result = client.requests[1]["messages"][-1]["content"][0]
    assert first_result["is_error"] is True
    assert "thickness_mm" in first_result["content"]


def test_loop_stops_after_max_steps() -> None:
    looping = [
        response("tool_use", tool_use(f"t{i}", "list_rules", {})) for i in range(MAX_STEPS + 1)
    ]
    reply = ask_with(FakeClient(*looping))
    assert len(reply.calls) == MAX_STEPS
    assert "Stopped after" in reply.stopped


def test_refusal_and_cut_off_answers_say_so() -> None:
    refused = ask_with(FakeClient(response("refusal")))
    assert refused.text == "" and "declined" in refused.stopped
    cut = ask_with(FakeClient(response("max_tokens", text("Part of"))))
    assert cut.text == "Part of" and "cut off" in cut.stopped


def test_api_failure_is_reported_not_raised() -> None:
    class Broken:
        @property
        def beta(self) -> Any:
            raise RuntimeError("no network")

    reply = ask("Hi", DesignInput(), [], STANDARDS, RULES, client=Broken())
    assert reply.error and reply.text == ""


def test_history_is_sent_as_plain_turns() -> None:
    client = FakeClient(response("end_turn", text("Yes.")))
    history = [ChatMessage(role="user", text="Hi"), ChatMessage(role="assistant", text="Hello")]
    ask("And now?", DesignInput(), history, STANDARDS, RULES, client=client)
    messages = client.requests[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


# --- numbers --------------------------------------------------------------------------


def test_numbers_from_tools_pass_and_invented_ones_are_flagged() -> None:
    sources = ['{"press_force_kn": 123.456, "utilisation_pct": 61.234}']
    answer = (
        "Press force is 123.5 kN (PT-TON-001), material use 61.2 %, on hole H2. "
        "Use 2 screws. The die will last 250,000 hits and needs 17.5 mm."
    )
    assert unverified_numbers(answer, sources) == ["250,000", "17.5"]


def test_answer_numbers_are_checked_against_the_tool_results() -> None:
    client = FakeClient(
        response("tool_use", tool_use("t1", "lookup_standard", {
            "table": "materials", "key": {"material": "mild_steel"}})),
        response("end_turn", text("Shear strength is 999.9 N/mm² per the table.")),
    )  # fmt: skip
    reply = ask_with(client)
    assert reply.unverified_numbers == ["999.9"]


# --- tools ----------------------------------------------------------------------------


def test_every_tool_schema_is_a_plain_object_schema() -> None:
    for tool in TOOLS.values():
        schema = tool.definition()["input_schema"]
        assert schema["type"] == "object"
        assert "$ref" not in json.dumps(schema) and "$defs" not in schema


def test_check_rules_tool() -> None:
    session = Session(design=DesignInput(), standards=STANDARDS, rules=RULES)
    subject = {
        "kind": "pierce_punch",
        "id": "P1",
        "values": {"material": "mild_steel", "thickness_mm": 2, "diameter_mm": 0.5},
    }
    call = run_tool(session, "check_rules", {"subjects": [subject]})
    statuses = {r["rule_id"]: r["status"] for r in call.output["results"]}
    assert statuses["PT-MIN-004"] == "warning"


def test_unknown_standards_table_lists_the_tables() -> None:
    session = Session(design=DesignInput(), standards=STANDARDS, rules=RULES)
    call = run_tool(session, "lookup_standard", {"table": "nope", "key": {}})
    assert call.is_error and "materials" in call.output["error"]


# --- chat panel and routes ------------------------------------------------------------


def test_panel_without_api_key_says_how_to_set_it_up() -> None:
    html = panel([], configured=False, design=DesignInput())
    assert "ANTHROPIC_API_KEY" in html
    assert "<textarea" not in html


def test_prose_is_escaped() -> None:
    html = prose("**Bold** <script>x</script>\n\n- one\n- `two`")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<strong>Bold</strong>" in html and "<li><code>two</code></li>" in html


def test_history_field_survives_a_round_trip_and_rejects_junk() -> None:
    history = [ChatMessage(role="user", text='a "quoted" <b>')]
    html = panel(history, configured=True, design=DesignInput())
    assert "&quot;quoted&quot;" in html
    assert parse_history('[{"role": "user", "text": "hi"}]')[0].text == "hi"
    assert parse_history("not json") == []
    assert parse_history('[{"role": "system", "text": "x"}]') == []


client = TestClient(app)


def test_home_page_shows_the_assistant_setup_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = client.get("/").text
    assert 'id="assistant"' in page and "ANTHROPIC_API_KEY" in page


def test_assistant_route_answers_with_the_design_from_the_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen: dict[str, Any] = {}

    def fake_ask(question: str, design: DesignInput, history: list[ChatMessage], *a: Any) -> Reply:
        seen.update(question=question, design=design, history=history)
        return Reply(
            text="Use **PR-400**.", proposal=design.model_copy(update={"punch_length_mm": 75})
        )

    monkeypatch.setattr(ai.assistant, "ask", fake_ask)
    form = {"message": "Which press?", "thickness_mm": "2", "history": ""}
    html = client.post("/ui/assistant", data=form).text
    assert seen["question"] == "Which press?" and seen["design"].thickness_mm == 2
    assert "<strong>PR-400</strong>" in html
    assert "Apply to design" in html and 'name="punch_length_mm" value="75"' in html
    assert "Which press?" in html  # the question is now in the log and the history field

    # Applying the proposal reloads the page with the new values and keeps the chat.
    history = json.dumps([{"role": "user", "text": "Which press?"},
                          {"role": "assistant", "text": "Use PR-400."}])  # fmt: skip
    page = client.post("/", data={"punch_length_mm": "75", "history": history}).text
    assert 'name="punch_length_mm" value="75"' in page
    assert "Use PR-400." in page


def test_assistant_route_without_key_does_not_call_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai.assistant, "ask", lambda *a: pytest.fail("called the API"))
    html = client.post("/ui/assistant", data={"message": "Hi"}).text
    assert "ANTHROPIC_API_KEY" in html
