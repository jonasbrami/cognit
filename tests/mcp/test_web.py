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
