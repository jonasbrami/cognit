import json
from typing import Any

import httpx
import pytest
import respx

from cognit.engine.llm import GenerateRequest
from cognit.engine.llm_anthropic import AnthropicLLM
from cognit.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizOutline

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
    monkeypatch.setattr("cognit.engine.llm_anthropic._load_claude_code_oauth", lambda: None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM()


def test_falls_back_to_claude_code_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "cognit.engine.llm_anthropic._load_claude_code_oauth", lambda: "fake-oauth-token"
    )
    llm = AnthropicLLM()
    assert llm._client.auth_token == "fake-oauth-token"


def test_expired_oauth_token_raises_with_specific_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """An expired OAuth token must NOT be reported as 'no credentials' — the user needs
    to know the actual cause so they run `claude login`."""
    import time

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Point the loader at a temp credentials file with an expired token.
    fake_creds = tmp_path / "credentials.json"  # type: ignore[attr-defined]
    fake_creds.write_text(
        '{"claudeAiOauth": {"accessToken": "expired", "expiresAt": '
        + str(int(time.time() * 1000) - 86_400_000)
        + "}}"
    )
    monkeypatch.setattr("cognit.engine.llm_anthropic._CLAUDE_CREDS_PATH", fake_creds)
    with pytest.raises(RuntimeError, match="expired"):
        AnthropicLLM()


@respx.mock
def test_outline_request_pins_tool_schema_and_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ensure the outline tool's input_schema is QuizOutline's
    (not Quiz's — easy mistake), and that tool_choice forces submit_quiz_outline.
    Without these the API may return a plain text block and _extract_tool_input crashes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json=_tool_use_response(_TOOL_OUTLINE, canned.model_dump())
        )
    )
    AnthropicLLM().generate_quiz_outline(
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={})
    )
    body = json.loads(route.calls.last.request.content)
    assert body["tool_choice"] == {"type": "tool", "name": _TOOL_OUTLINE}
    tool = body["tools"][0]
    assert tool["name"] == _TOOL_OUTLINE
    # Outline schema must define MermaidPlaceholder, not MermaidQuestion. Checking the
    # $defs map (not substring search) avoids false positives from docstrings/comments.
    defs = tool["input_schema"].get("$defs", {})
    assert "MermaidPlaceholder" in defs, "outline schema should define MermaidPlaceholder"
    assert "MermaidQuestion" not in defs, (
        "outline schema must not define MermaidQuestion (that's the post-render type)"
    )


@respx.mock
def test_oauth_token_rotation_recovers_via_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code rotates OAuth tokens in `~/.claude/.credentials.json` while cognit holds
    an in-memory client built from the older token. On a 401, the client should re-read
    credentials, rebuild, and retry once — so a long-running `cognit take` session doesn't
    fail to grade just because the user's local credentials refreshed in the meantime."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tokens = iter(["old-token", "new-token"])
    monkeypatch.setattr(
        "cognit.engine.llm_anthropic._load_claude_code_oauth",
        lambda: next(tokens),
    )
    responses = iter(
        [
            httpx.Response(
                401,
                json={
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "Invalid"},
                },
            ),
            httpx.Response(
                200, json=_tool_use_response(_TOOL_GRADE, {"score": 80, "feedback": "ok"})
            ),
        ]
    )
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        side_effect=lambda req: next(responses)
    )
    llm = AnthropicLLM()
    score, fb = llm.grade_open("why?", "r", "because")
    assert route.call_count == 2
    assert (score, fb) == (80, "ok")
    assert llm._client.auth_token == "new-token"


@respx.mock
def test_api_key_auth_does_not_retry_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """API keys don't rotate — a 401 is a real configuration problem, not a recoverable
    rotation. The retry path is OAuth-only; an API-key 401 must bubble up unchanged."""
    from anthropic import AuthenticationError

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "Invalid"},
            },
        )
    )
    llm = AnthropicLLM()
    with pytest.raises(AuthenticationError):
        llm.grade_open("why?", "r", "because")
    assert route.call_count == 1


@respx.mock
def test_outline_and_mermaid_use_distinct_system_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache-control benefit only materializes if each stage's system prompt is
    a stable, distinct string. If both stages accidentally send the same system text,
    we'd lose the per-stage focus AND the test that asserts cache_control would still
    pass — so check the texts actually differ."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    canned_outline = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    canned_mset = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nD-->A",
        },
        correct="A",
    )

    captured: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body["system"][0]["text"])
        # Return the appropriate tool based on tool_choice.
        tool_name = body["tool_choice"]["name"]
        if tool_name == _TOOL_OUTLINE:
            return httpx.Response(
                200, json=_tool_use_response(_TOOL_OUTLINE, canned_outline.model_dump())
            )
        return httpx.Response(200, json=_tool_use_response(_TOOL_MERMAID, canned_mset.model_dump()))

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_route)

    llm = AnthropicLLM()
    llm.generate_quiz_outline(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))
    llm.generate_mermaid_set(
        MermaidSpec(
            diagram_type="flowchart",
            correct_description="x",
            misconceptions=["a", "b", "c"],
            style_notes="n",
        ),
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}),
    )
    assert len(captured) == 2
    assert captured[0] != captured[1], "outline and mermaid system prompts must differ"
