import json
from typing import Any

import httpx
import pytest
import respx

from quizz.engine.llm import GenerateRequest
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizOutline

_TOOL_OUTLINE = "submit_quiz_outline"
_TOOL_MERMAID = "submit_mermaid_set"
_TOOL_GRADE = "submit_grade"


def _tool_use_response(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Build a fake Anthropic Messages response containing a tool_use block."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [
            {
                "type": "tool_use",
                "id": "tu_test",
                "name": tool_name,
                "input": tool_input,
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


@respx.mock
def test_generate_quiz_outline_via_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json=_tool_use_response(_TOOL_OUTLINE, canned.model_dump())
        )
    )
    llm = AnthropicLLM()
    out = llm.generate_quiz_outline(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))
    assert route.called
    assert out == canned


@respx.mock
def test_generate_quiz_outline_sends_system_with_cache_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request body must include a `system` parameter as a list of blocks, each
    carrying `cache_control: ephemeral`. This is what enables prompt caching across the
    outline call and the subsequent mermaid artisan calls in the same run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json=_tool_use_response(_TOOL_OUTLINE, canned.model_dump())
        )
    )
    llm = AnthropicLLM()
    llm.generate_quiz_outline(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))

    body = json.loads(route.calls.last.request.content)
    assert isinstance(body["system"], list), (
        "system must be a list-of-blocks to attach cache_control"
    )
    assert body["system"][0]["type"] == "text"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    # The system text should contain content from system_generate.txt — sanity check.
    assert "comprehension quiz author" in body["system"][0]["text"].lower()


@respx.mock
def test_generate_mermaid_set_via_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    canned_set = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nD-->A",
        },
        correct="A",
    )
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json=_tool_use_response(_TOOL_MERMAID, canned_set.model_dump())
        )
    )
    llm = AnthropicLLM()
    out = llm.generate_mermaid_set(
        MermaidSpec(
            diagram_type="flowchart",
            correct_description="A calls B",
            misconceptions=["B calls A", "no call", "extra C"],
            style_notes="2 nodes, LR",
        ),
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}),
    )
    assert route.called
    assert out == canned_set
    # Mermaid artisan also gets a system block with cache_control.
    body = json.loads(route.calls.last.request.content)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "mermaid" in body["system"][0]["text"].lower()


@respx.mock
def test_grade_open_via_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json=_tool_use_response(_TOOL_GRADE, {"score": 75, "feedback": "good"})
        )
    )
    llm = AnthropicLLM()
    score, fb = llm.grade_open("why?", "r", "because")
    assert route.called
    assert score == 75
    assert fb == "good"
    # Grader has a system block with cache_control too.
    body = json.loads(route.calls.last.request.content)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_missing_credentials_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("quizz.engine.llm_anthropic._load_claude_code_oauth", lambda: None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM()


def test_falls_back_to_claude_code_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "quizz.engine.llm_anthropic._load_claude_code_oauth", lambda: "fake-oauth-token"
    )
    llm = AnthropicLLM()
    assert llm._client.auth_token == "fake-oauth-token"
