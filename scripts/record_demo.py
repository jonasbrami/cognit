"""Record the README demo GIF of the `cognit take` browser flow.

Boots the FastAPI app with a polished inline demo quiz and a FAKE LLM (no Claude
tokens, no `gh` auth, no network), starts in the "generating" phase so the GIF
tells the full story, then drives Chromium via Playwright through a natural-paced
run:

    command → Claude "generating" activity feed → quiz renders → author answers
    all four question types → Submit → results / scores

Playwright records the run to a `.webm`, which ffmpeg converts to an optimized,
looping GIF at `docs/img/cognit-demo.gif` (two-pass palettegen/paletteuse).

Everything runs offline and deterministically — this is the same fake-server
pattern the test suite uses (see tests/conftest.py and tests/server/).

Prerequisites
-------------
The Chromium browser Playwright drives must be installed once:

    uv run playwright install chromium

ffmpeg must be on PATH (used for the webm → gif conversion).

Usage
-----
    uv run python scripts/record_demo.py

    # or the thin wrapper:
    scripts/record-demo.sh

Re-run after any UI change in src/cognit/server/assets/. Output overwrites
docs/img/cognit-demo.gif; temp video files are cleaned up automatically.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import sync_playwright

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import (
    MCQQuestion,
    MermaidQuestion,
    OpenQuestion,
    Quiz,
    TrueFalseQuestion,
)
from cognit.server.app import build_app

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "docs" / "img" / "cognit-demo.gif"

# Crisp viewport; device_scale_factor=2 renders at 2x for sharpness, then ffmpeg
# scales the recording down to GIF_WIDTH with lanczos.
VIEWPORT = {"width": 1280, "height": 800}
DEVICE_SCALE_FACTOR = 2

GIF_WIDTH = 1000
GIF_FPS = 14

PR_URL = "https://github.com/jonasbrami/cognit/pull/142"


def _demo_quiz() -> Quiz:
    """A representative PR quiz: one of each question type, believable prompts,
    and a 4-option mermaid-pick (A/B/C/D) to match the real product."""
    return Quiz(
        pr_number=142,
        questions=[
            MCQQuestion(
                id="q1",
                prompt="When `rate_limit_exceeded(key)` returns True, what does the middleware do?",
                options=[
                    "Raises HTTPException(429) and lets the handler catch it",
                    "Returns JSONResponse(status_code=429) with a Retry-After header",
                    "Logs a warning and passes the request through",
                    "Increments a counter and continues to the route",
                ],
                answer="Returns JSONResponse(status_code=429) with a Retry-After header",
                explanation=(
                    "It returns a JSONResponse directly — the middleware short-circuits "
                    "and never lets the rate-limited request reach the route handler."
                ),
            ),
            MermaidQuestion(
                id="q2",
                prompt="Which diagram matches the request path through the new middleware stack?",
                options={
                    "A": "flowchart LR\n  R[request]-->A[auth]-->L[rate limit]-->H[route]",
                    "B": "flowchart LR\n  R[request]-->L[rate limit]-->A[auth]-->H[route]",
                    "C": "flowchart LR\n  R[request]-->H[route]-->A[auth]-->L[rate limit]",
                    "D": "flowchart LR\n  R[request]-->A[auth]-->H[route]-->L[rate limit]",
                },
                answer="A",
                explanation="Auth runs first, then the rate limiter, then the route.",
            ),
            OpenQuestion(
                id="q3",
                prompt="Why does the counter store use Redis instead of an in-process dict?",
                rubric="must mention shared state across worker processes",
            ),
            TrueFalseQuestion(
                id="q4",
                prompt="The `@skip_rate_limit` decorator bypasses the middleware entirely.",
                answer=False,
                explanation=(
                    "It only sets a flag the middleware reads — the request still passes "
                    "through the middleware, which then chooses not to count it."
                ),
            ),
        ],
    )


def _stream_generation(broker, quiz: Quiz) -> None:
    """Emit a realistic 'Claude is generating' activity feed into the broker,
    then flip it to ready. Mirrors the event kinds quiz.js renders (step / text /
    tool_use). Runs on a background thread so the page polls /progress live."""
    feed = [
        ({"kind": "step", "tool": "submit_quiz"}, 0.5),
        ({"kind": "text", "text": "Reading the diff for PR #142…", "tool": "submit_quiz"}, 0.7),
        (
            {"kind": "tool_use", "name": "read_file", "detail": "src/middleware/rate_limit.py"},
            0.6,
        ),
        ({"kind": "tool_use", "name": "read_file", "detail": "src/app.py"}, 0.5),
        (
            {
                "kind": "text",
                "text": "Auth runs before the limiter; counters live in Redis. Drafting questions…",
                "tool": "submit_quiz",
            },
            0.9,
        ),
        ({"kind": "tool_use", "name": "grep", "detail": "skip_rate_limit"}, 0.5),
        ({"kind": "text", "text": "Writing 4 questions across the changed paths.", "tool": "submit_quiz"}, 0.8),
    ]
    for event, delay in feed:
        broker.emit(event)
        time.sleep(delay)
    broker.set_ready(quiz)


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


def _record_webm(base_url: str, video_dir: Path) -> Path:
    """Drive the browser through the demo and return the recorded .webm path."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            record_video_dir=str(video_dir),
            record_video_size=VIEWPORT,
        )
        page = ctx.new_page()

        # --- Generating intro: the live activity feed while Claude "writes" the quiz.
        # Don't wait for networkidle — the page polls /progress continuously here.
        page.goto(base_url, wait_until="load")
        page.wait_for_selector("#term-feed .term__line", timeout=5000)
        page.wait_for_timeout(2500)  # let a few feed lines stream in

        # --- Quiz renders once the broker flips to ready.
        page.wait_for_selector("#questions-root .file .option", timeout=8000)
        page.wait_for_selector("#questions-root .diagram svg", timeout=10000)
        page.wait_for_timeout(1200)

        # --- Q1 MCQ: pick the correct option (index 1).
        page.locator("#questions-root .file").nth(0).locator(".option").nth(1).click()
        page.wait_for_timeout(900)

        # --- Q2 mermaid: scroll into view, pick diagram A (correct, first card).
        q2 = page.locator("#questions-root .file").nth(1)
        q2.scroll_into_view_if_needed()
        page.wait_for_timeout(700)
        q2.locator(".diagram").first.click()
        page.wait_for_timeout(900)

        # --- Q3 open: type a believable answer at a natural pace.
        q3 = page.locator("#questions-root .file").nth(2)
        q3.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        q3.locator("textarea").click()
        q3.locator("textarea").type(
            "Each worker is a separate process, so an in-process dict wouldn't share "
            "counters. Redis is a single source of truth across all workers.",
            delay=18,
        )
        page.wait_for_timeout(800)

        # --- Q4 true/false: pick False (index 1, correct).
        q4 = page.locator("#questions-root .file").nth(3)
        q4.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        q4.locator(".tf__cell").nth(1).click()
        page.wait_for_timeout(900)

        # --- Submit and land on results; pause so scores are readable.
        page.locator("#reviewbar button.btn--primary").click()
        page.wait_for_selector("#questions-root .summary", timeout=8000)
        page.wait_for_selector("#questions-root .file.ok", timeout=8000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(3000)  # hold on the summary/scores

        video = page.video
        ctx.close()
        browser.close()
        if video is None:
            raise RuntimeError("Playwright did not record a video")
        return Path(video.path())


def _webm_to_gif(webm: Path, out: Path) -> None:
    """Two-pass palettegen/paletteuse → optimized, looping GIF."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        palette = Path(tmp) / "palette.png"
        vf_common = f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos"
        # Pass 1: build an optimized palette from the whole clip.
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(webm),
                "-vf", f"{vf_common},palettegen=stats_mode=diff",
                str(palette),
            ],
            check=True,
            capture_output=True,
        )
        # Pass 2: apply the palette with dithering; loop forever.
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(webm), "-i", str(palette),
                "-lavfi", f"{vf_common} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3",
                "-loop", "0",
                str(out),
            ],
            check=True,
            capture_output=True,
        )


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found on PATH — install it to build the GIF.")

    quiz = _demo_quiz()
    # Start in the "generating" phase (quiz=None) so the GIF captures the streaming
    # activity feed before the quiz appears. A background thread streams events and
    # then flips the broker to ready.
    app = build_app(
        quiz=None,
        pr_number=142,
        pr_url=PR_URL,
        llm=FakeLLM(canned_open_score=85, canned_open_feedback="Captures the key idea."),
        post_comment=lambda body: f"{PR_URL}#issuecomment-9999",
    )
    threading.Thread(
        target=_stream_generation, args=(app.state.broker, quiz), daemon=True
    ).start()

    port = _free_port()
    server = _serve(app, port)
    base = f"http://127.0.0.1:{port}"

    video_dir = Path(tempfile.mkdtemp(prefix="cognit-demo-"))
    try:
        webm = _record_webm(base, video_dir)
        _webm_to_gif(webm, OUT_PATH)
        size_mb = OUT_PATH.stat().st_size / 1_000_000
        print(f"wrote {OUT_PATH} ({size_mb:.2f} MB)")
    finally:
        server.should_exit = True
        shutil.rmtree(video_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
