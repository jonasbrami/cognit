import httpx
import pytest
import respx

from quizz.engine.llm import GenerateRequest
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.engine.models import MCQQuestion, Quiz

_QUIZ_TOOL_NAME = "submit_quiz"
_GRADE_TOOL_NAME = "submit_grade"


def _quiz_response(quiz: Quiz) -> dict:  # type: ignore[type-arg]
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
                "name": _QUIZ_TOOL_NAME,
                "input": quiz.model_dump(),
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def _grade_response(score: int, feedback: str) -> dict:  # type: ignore[type-arg]
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [
            {
                "type": "tool_use",
                "id": "tu_test",
                "name": _GRADE_TOOL_NAME,
                "input": {"score": score, "feedback": feedback},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 20},
    }


@respx.mock
def test_generate_quiz_via_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    canned = Quiz(
        pr_number=42,
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=_quiz_response(canned))
    )
    llm = AnthropicLLM()
    out = llm.generate_quiz(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))
    assert route.called
    assert out == canned


@respx.mock
def test_grade_open_via_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=_grade_response(score=75, feedback="good"))
    )
    llm = AnthropicLLM()
    score, fb = llm.grade_open("why?", "r", "because")
    assert score == 75
    assert fb == "good"


def test_missing_credentials_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both ANTHROPIC_API_KEY unset AND no Claude Code OAuth available."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("quizz.engine.llm_anthropic._load_claude_code_oauth", lambda: None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM()


def test_falls_back_to_claude_code_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ANTHROPIC_API_KEY is unset, uses ~/.claude/.credentials.json OAuth token."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "quizz.engine.llm_anthropic._load_claude_code_oauth", lambda: "fake-oauth-token"
    )
    llm = AnthropicLLM()
    # Anthropic SDK stores it in the client config; verify via the bearer auth header
    assert llm._client.auth_token == "fake-oauth-token"
