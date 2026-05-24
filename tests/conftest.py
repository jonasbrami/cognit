import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, MermaidQuestion, OpenQuestion, Quiz, TrueFalseQuestion
from cognit.server.app import build_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(app: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    last_exc: Exception | None = None
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=0.3) as c:
                if c.get(f"http://127.0.0.1:{port}/static/styles.css").status_code == 200:
                    return server, thread
        except Exception as exc:
            last_exc = exc
        time.sleep(0.05)
    raise RuntimeError(
        f"uvicorn on port {port} did not become ready within 5s (last error: {last_exc!r})"
    )


@pytest.fixture
def sample_quiz() -> Quiz:
    return Quiz(
        pr_number=142,
        questions=[
            MCQQuestion(
                id="q1",
                prompt="When `rate_limit_exceeded(key)` returns True, the middleware…",
                options=[
                    "raises HTTPException(429)",
                    "returns JSONResponse(status_code=429) with a Retry-After header",
                    "logs a warning and passes through",
                    "increments a counter and continues",
                ],
                answer="returns JSONResponse(status_code=429) with a Retry-After header",
                explanation="It returns a JSONResponse, not a raised exception — the middleware never lets the request through.",
            ),
            MermaidQuestion(
                id="q2",
                prompt="Which diagram matches the actual request path?",
                options={
                    "A": "flowchart LR; A[req]-->B[auth]-->C[limit]-->D[route]",
                    "B": "flowchart LR; A[req]-->B[limit]-->C[auth]-->D[route]",
                },
                answer="A",
            ),
            OpenQuestion(id="q3", prompt="Why Redis over a dict?", rubric="cross-worker state"),
            TrueFalseQuestion(
                id="q4", prompt="`@skip_rate_limit` bypasses the middleware entirely.", answer=False
            ),
        ],
    )


@pytest.fixture
def live_server(sample_quiz: Quiz) -> Iterator[tuple[str, list[str]]]:
    """Run the FastAPI app on a random local port. Yields (base_url, posted_bodies)."""
    posted: list[str] = []

    def fake_post(body: str) -> str:
        posted.append(body)
        return "https://github.com/jonas/cognit/pull/142#issuecomment-9999"

    app = build_app(
        quiz=sample_quiz,
        pr_url="https://github.com/jonas/cognit/pull/142",
        llm=FakeLLM(canned_open_score=80, canned_open_feedback="reasonable"),
        post_comment=fake_post,
    )
    port = _free_port()
    server, thread = _start_server(app, port)
    try:
        yield f"http://127.0.0.1:{port}", posted
    finally:
        server.should_exit = True
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError(
                f"uvicorn thread did not exit within 2s after should_exit on port {port}"
            )
