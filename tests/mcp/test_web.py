import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from cognit.engine.models import MCQQuestion, Quiz
from cognit.mcp.state import QuizState
from cognit.mcp.web import build_web_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _serve(app: FastAPI, port: int):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/state", timeout=0.3).status_code == 200:
                return server, t
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture
def client(tmp_path: Path):
    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    state.set_quiz(Quiz(pr_number=7, questions=[
        MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A", explanation="x")]))
    posted: list[str] = []
    app = build_web_app(state, post_comment=lambda b: (posted.append(b), "http://c/1")[1])
    port = _free_port()
    server, t = _serve(app, port)
    try:
        yield httpx.Client(base_url=f"http://127.0.0.1:{port}"), state, posted
    finally:
        server.should_exit = True
        t.join(timeout=2)


def test_state_serves_quiz(client):
    c, _state, _ = client
    body = c.get("/state").json()
    assert body["quiz"]["questions"][0]["id"] == "q1"
    assert body["answers"] == {}


def test_post_answer_records(client):
    c, state, _ = client
    assert c.post("/answer", json={"question_id": "q1", "value": "A"}).status_code == 200
    assert state.answers == {"q1": "A"}


def test_publish_calls_post_comment(client):
    c, state, posted = client
    from cognit.engine.models import Results, QuestionResult
    state.set_results(Results(pr_number=7, total_score=100,
                              per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")]))
    r = c.post("/publish")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(posted) == 1


def test_index_serves_page(client):
    c, _state, _ = client
    html = c.get("/").text
    assert "<html" in html.lower() or "<!doctype" in html.lower()
    assert "__PR__" not in html  # chrome placeholders are templated out
    assert "#7" in html  # pr_number rendered into the chrome


def test_grade_endpoint_grades_and_stores(tmp_path: Path) -> None:
    from cognit.engine.models import QuestionResult, Results

    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    state.set_quiz(Quiz(pr_number=7, questions=[
        MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A", explanation="x")]))

    def fake_grade() -> Results:
        r = Results(pr_number=7, total_score=100,
                    per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")])
        state.set_results(r)  # handler-owned: real grade_state stores into state too
        return r

    app = build_web_app(state, post_comment=lambda b: "http://c/1", grade=fake_grade)
    port = _free_port()
    server, t = _serve(app, port)
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/grade")
        assert r.status_code == 200
        assert r.json()["total_score"] == 100
        assert state.results is not None and state.results.total_score == 100
    finally:
        server.should_exit = True
        t.join(timeout=2)


def test_grade_endpoint_501_when_unavailable(client):
    # the default fixture app wires no `grade` callable
    c, _state, _ = client
    assert c.post("/grade").status_code == 501


def test_publish_before_grading_409(tmp_path: Path) -> None:
    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    state.set_quiz(Quiz(pr_number=7, questions=[
        MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A", explanation="x")]))
    app = build_web_app(state, post_comment=lambda b: "http://c/1")
    port = _free_port()
    server, t = _serve(app, port)
    try:
        assert httpx.get(f"http://127.0.0.1:{port}/state").status_code == 200
        r = httpx.post(f"http://127.0.0.1:{port}/publish")
        assert r.status_code == 409
    finally:
        server.should_exit = True
        t.join(timeout=2)
