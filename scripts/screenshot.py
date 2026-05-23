"""Generate README screenshots of the quiz UI.

Boots the FastAPI app with a fixture quiz, drives playwright to capture
`docs/img/quizz-questions.png` (initial form) and `docs/img/quizz-results.png`
(post-submit results view).

Usage:
    uv run python scripts/screenshot.py

Re-run after any UI change in src/quizz/server/assets/.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import sync_playwright

from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import (
    MCQQuestion,
    MermaidQuestion,
    OpenQuestion,
    Quiz,
    TrueFalseQuestion,
)
from quizz.server.app import build_app


OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "img"
VIEWPORT = {"width": 1280, "height": 900}


def _fixture_quiz() -> Quiz:
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
            ),
            MermaidQuestion(
                id="q2",
                prompt="Which diagram matches the actual request path?",
                options={
                    "A": "flowchart LR\n  R[req]-->A[auth]-->L[limit]-->H[route]",
                    "B": "flowchart LR\n  R[req]-->L[limit]-->A[auth]-->H[route]",
                    "C": "flowchart LR\n  R[req]-->H[route]-->A[auth]-->L[limit]",
                    "D": "flowchart LR\n  R[req]-->A[auth]-->H[route]-->L[limit]",
                },
                answer="A",
            ),
            OpenQuestion(
                id="q3",
                prompt="Why Redis over an in-process dict for the counter store?",
                rubric="must mention shared state across worker processes",
            ),
            TrueFalseQuestion(
                id="q4",
                prompt="`@skip_rate_limit` bypasses the middleware entirely.",
                answer=False,
            ),
        ],
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=0.3) as c:
                if c.get(f"http://127.0.0.1:{port}/static/styles.css").status_code == 200:
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.05)
    raise RuntimeError(f"uvicorn did not become ready on port {port}: {last!r}")


def _serve(app: FastAPI, port: int) -> uvicorn.Server:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    _wait_ready(port)
    return server


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    quiz = _fixture_quiz()
    app = build_app(
        quiz=quiz,
        pr_url="https://github.com/jonasbrami/quizz/pull/142",
        llm=FakeLLM(canned_open_score=80, canned_open_feedback="Captures the key idea."),
        post_comment=lambda body: "https://github.com/jonasbrami/quizz/pull/142#issuecomment-9999",
    )
    port = _free_port()
    server = _serve(app, port)
    base = f"http://127.0.0.1:{port}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
            page = ctx.new_page()

            page.goto(base, wait_until="networkidle")
            # Wait for at least one mermaid SVG to finish rendering.
            page.wait_for_selector("#questions-root .diagram svg", timeout=10_000)
            # Pick the correct MCQ option so the screenshot shows a "real" interaction state.
            page.locator("#questions-root .file").nth(0).locator(".option").nth(1).click()
            page.locator("#questions-root .file").nth(1).locator(".diagram").first.click()
            page.locator("#questions-root .file").nth(2).locator("textarea").fill(
                "Each gunicorn worker is its own process — a dict would not share counters "
                "across them. Redis gives us a single source of truth."
            )
            page.locator("#questions-root .file").nth(3).locator(".tf__cell").nth(1).click()

            questions_path = OUT_DIR / "quizz-questions.png"
            page.screenshot(path=str(questions_path), full_page=True)
            print(f"wrote {questions_path}")

            # Submit and capture results.
            page.locator("#reviewbar button.btn--primary").click()
            page.wait_for_selector("#questions-root .summary", timeout=10_000)
            # Let the post-submit mermaid re-render settle.
            page.wait_for_selector("#questions-root .file.ok", timeout=10_000)
            page.evaluate("window.scrollTo(0, 0)")

            results_path = OUT_DIR / "quizz-results.png"
            page.screenshot(path=str(results_path), full_page=True)
            print(f"wrote {results_path}")

            ctx.close()
            browser.close()
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
