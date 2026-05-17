import pytest
import respx
import httpx
from quizz.engine.llm import GenerateRequest
from quizz.engine.llm_githubmodels import GitHubModelsLLM
from quizz.engine.models import Quiz, MCQQuestion


@respx.mock
def test_generate_quiz_hits_models_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    canned = Quiz(
        pr_number=42,
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    route = respx.post("https://models.github.ai/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": canned.model_dump_json()},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    llm = GitHubModelsLLM()
    out = llm.generate_quiz(
        GenerateRequest(
            diff="x",
            pr_title="t",
            pr_body="b",
            files={},
        )
    )
    assert route.called
    assert out == canned


@respx.mock
def test_grade_open_returns_score_and_feedback(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    respx.post("https://models.github.ai/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"score": 85, "feedback": "good"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    llm = GitHubModelsLLM()
    score, fb = llm.grade_open(question_prompt="why?", rubric="r", answer="x")
    assert score == 85
    assert fb == "good"


def test_missing_token_raises_useful_error(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        GitHubModelsLLM()
