from fastapi.testclient import TestClient

from quizz.comment.render import render_results
from quizz.engine.models import MCQQuestion, OpenQuestion, Quiz, Results, QuestionResult
from quizz.server.app import build_app


def _sample_quiz() -> Quiz:
    return Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A"),
            OpenQuestion(id="q2", prompt="explain", rubric="r"),
        ],
    )


def test_get_root_renders_quiz() -> None:
    app = build_app(
        quiz=_sample_quiz(), pr_url="https://github.com/o/r/pull/42", post_answers=lambda md: None
    )
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "why?" in r.text
    assert "<!doctype html>" in r.text.lower()


def test_static_assets_served() -> None:
    app = build_app(quiz=_sample_quiz(), pr_url="x", post_answers=lambda md: None)
    client = TestClient(app)
    assert client.get("/static/quiz.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_submit_grades_deterministic_and_posts(monkeypatch: object) -> None:
    posted: list[str] = []
    app = build_app(
        quiz=_sample_quiz(),
        pr_url="x",
        post_answers=lambda md: posted.append(md),
    )
    client = TestClient(app)
    payload = {
        "version": "1",
        "pr_number": 42,
        "entries": [
            {"question_id": "q1", "value": "A"},  # correct
            {"question_id": "q2", "value": "some answer"},  # open: scored 0 immediately
        ],
    }
    r = client.post("/submit", json=payload)
    assert r.status_code == 200
    data = r.json()
    # deterministic score considers only non-open questions: q1 correct = 100
    assert data["deterministic_score"] == 100
    assert len(posted) == 1
    assert "<!-- quizz:answers v1 -->" in posted[0]


def test_results_endpoint_not_ready(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "quizz.server.app.find_latest_marker_comment",
        lambda pr, marker: None,
    )
    app = build_app(quiz=_sample_quiz(), pr_url="x", post_answers=lambda md: None)
    client = TestClient(app)
    r = client.get("/results")
    assert r.status_code == 200
    assert r.json() == {"ready": False}


def test_results_endpoint_ready(monkeypatch: object) -> None:
    res = Results(
        pr_number=42,
        total_score=80,
        per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")],
    )
    monkeypatch.setattr(
        "quizz.server.app.find_latest_marker_comment",
        lambda pr, marker: render_results(res),
    )
    app = build_app(quiz=_sample_quiz(), pr_url="x", post_answers=lambda md: None)
    client = TestClient(app)
    r = client.get("/results")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["results"]["total_score"] == 80
