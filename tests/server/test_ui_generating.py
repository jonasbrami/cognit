"""Playwright test for the generating state: the browser shows a live activity
feed while generation runs on a background thread, then swaps to the quiz when
the broker flips to ready. Exercises the real /progress polling loop in quiz.js."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import sync_playwright

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, OpenQuestion, Quiz
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
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=0.3) as c:
                if c.get(f"http://127.0.0.1:{port}/static/styles.css").status_code == 200:
                    return server, thread
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        try:
            yield pg
        finally:
            ctx.close()
            browser.close()


def test_generating_feed_then_swaps_to_quiz(page) -> None:
    quiz = Quiz(
        pr_number=99,
        questions=[
            MCQQuestion(id="q1", prompt="why?", options=["A", "B", "C", "D"], answer="A"),
            OpenQuestion(id="q2", prompt="explain", rubric="r"),
        ],
    )
    release = threading.Event()

    def on_generate(broker) -> None:  # type: ignore[no-untyped-def]
        broker.emit({"kind": "step", "tool": "submit_quiz_outline"})
        broker.emit({"kind": "text", "text": "reading the diff…", "tool": "submit_quiz_outline"})
        release.wait(timeout=5)
        broker.set_ready(quiz)

    app = build_app(
        quiz=None,
        pr_number=99,
        pr_url="https://github.com/o/r/pull/99",
        llm=FakeLLM(),
        post_comment=lambda md: "https://x/y#1",
    )
    threading.Thread(target=on_generate, args=(app.state.broker,), daemon=True).start()
    port = _free_port()
    server, thread = _start_server(app, port)
    try:
        # Don't wait for networkidle — the page polls /progress continuously.
        page.goto(f"http://127.0.0.1:{port}", wait_until="load")

        # The generating view appears with the streamed activity feed.
        page.wait_for_selector("#term-feed .term__line", timeout=5000)
        feed = page.locator("#term-feed").text_content()
        assert "Generating outline" in feed  # step → friendly label
        assert "reading the diff" in feed  # streamed assistant text
        # The quiz is not rendered yet.
        assert page.locator("#questions-root .file").count() == 0

        # Let generation finish; the next poll flips the page to the quiz.
        release.set()
        page.wait_for_selector("#questions-root .file .option", timeout=5000)
        assert page.locator("#questions-root .file").count() == 2
        assert page.locator("#reviewbar button").get_by_text("Submit", exact=False).is_visible()
    finally:
        server.should_exit = True
        thread.join(timeout=2)
