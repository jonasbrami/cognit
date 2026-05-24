"""Tests for the streaming surface added to the app: GET /progress and the
generating-mode root page (quiz=None)."""

from fastapi.testclient import TestClient

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, Quiz
from cognit.server.app import build_app


def _quiz() -> Quiz:
    return Quiz(
        pr_number=42,
        questions=[MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A")],
    )


def _app_generating() -> object:
    return build_app(
        quiz=None,
        pr_number=42,
        pr_url="https://github.com/o/r/pull/42",
        llm=FakeLLM(),
        post_comment=lambda md: "https://x/y#1",
    )


def test_progress_replays_events_from_cursor() -> None:
    app = _app_generating()
    broker = app.state.broker  # type: ignore[attr-defined]
    broker.emit({"kind": "step", "tool": "submit_quiz"})
    broker.emit({"kind": "text", "text": "picking…", "tool": "submit_quiz"})

    client = TestClient(app)
    r = client.get("/progress?cursor=0")
    assert r.status_code == 200
    data = r.json()
    assert data["phase"] == "generating"
    assert [e["kind"] for e in data["events"]] == ["step", "text"]
    assert data["next_cursor"] == 2
    assert data["quiz"] is None

    # A poller that already consumed both events sees only the tail.
    tail = client.get("/progress?cursor=2").json()
    assert tail["events"] == []
    assert tail["next_cursor"] == 2


def test_progress_returns_quiz_when_ready() -> None:
    app = _app_generating()
    app.state.broker.set_ready(_quiz())  # type: ignore[attr-defined]
    client = TestClient(app)
    data = client.get("/progress?cursor=0").json()
    assert data["phase"] == "ready"
    assert data["quiz"]["pr_number"] == 42


def test_progress_reports_error_phase() -> None:
    app = _app_generating()
    app.state.broker.set_error("claude binary not found")  # type: ignore[attr-defined]
    data = TestClient(app).get("/progress?cursor=0").json()
    assert data["phase"] == "error"
    assert data["error"] == "claude binary not found"


def test_index_generating_mode_injects_phase_and_null_quiz() -> None:
    client = TestClient(_app_generating())
    html = client.get("/").text
    assert 'window.PHASE = "generating"' in html
    assert "window.QUIZ = null" in html
    # PR chrome still renders without a quiz object.
    assert "#42" in html
    assert "https://github.com/o/r/pull/42" in html


def test_index_ready_mode_injects_quiz_and_ready_phase() -> None:
    app = build_app(
        quiz=_quiz(),
        pr_url="https://github.com/o/r/pull/42",
        llm=FakeLLM(),
        post_comment=lambda md: "https://x/y#1",
    )
    html = TestClient(app).get("/").text
    assert 'window.PHASE = "ready"' in html
    assert '"pr_number": 42' in html or '"pr_number":42' in html
    assert "why?" in html
