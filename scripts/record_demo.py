"""Record the README demo GIF of the `cognit take` browser flow.

Boots the FastAPI app with a **real, cognit-generated** quiz (captured from a live
PR by `scripts/capture_demo_quiz.py` into `scripts/demo_data/`) and a FAKE LLM for
grading only — no Claude tokens, no `gh` auth, no network. It starts in the
"generating" phase and replays the actual activity feed Claude emitted while
reading the diff, so the GIF tells the full story:

    command → Claude's real "generating" activity feed → quiz renders → author
    answers every question (answers computed from the quiz, so this adapts to
    whatever mix/order the model produced) → Submit → results / scores

Playwright records the run to a `.webm`, which ffmpeg converts to an optimized,
looping GIF at `docs/img/cognit-demo.gif` (two-pass palettegen/paletteuse). The
content is genuine; the playback is deterministic and offline, so re-recording
after a UI change needs no Claude call.

Prerequisites
-------------
    uv run playwright install chromium        # once
    ffmpeg on PATH
    scripts/demo_data/{quiz.json,feed.json}   # via scripts/capture_demo_quiz.py

Usage
-----
    uv run python scripts/record_demo.py      # or scripts/record-demo.sh

Re-run after any UI change in src/cognit/server/assets/. Output overwrites
docs/img/cognit-demo.gif; temp video files are cleaned up automatically.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Locator, Page, sync_playwright

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
DEMO_DATA = Path(__file__).resolve().parent / "demo_data"

# High-resolution capture. Playwright records the page at the *CSS viewport*
# resolution: device_scale_factor enlarges the backing store but NOT the screencast,
# and a record size larger than the viewport just pads the frame with empty canvas.
# So the viewport IS the resolution lever — we render and record 1:1 at 1440×900 and
# emit the GIF at that width with no downscale softening.
VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE_FACTOR = 2
RECORD_SIZE = VIEWPORT

GIF_WIDTH = 1440
GIF_FPS = 16

PR_URL = "https://github.com/jonasbrami/cognit/pull/20"

# Believable open-answer text, keyed by the captured question id. Filled in to
# match whatever open question the model wrote; a generic fallback is used for any
# id not listed so the recorder never stalls on an empty textarea.
OPEN_ANSWERS: dict[str, str] = {}
_OPEN_FALLBACK = (
    "It's a transient upstream failure — a rate limit or a brief overload — so a "
    "short backed-off retry is likely to succeed, whereas a malformed submission "
    "would just fail again."
)

# Generating-phase feed: keep it short and legible. Cap the number of lines and
# truncate long thinking/text so the activity feed reads as a quick montage.
MAX_FEED_LINES = 9
MAX_TEXT_CHARS = 130
_DELAY_BY_KIND = {"step": 0.5, "tool_use": 0.55, "text": 0.85, "thinking": 0.9}


# ── data loading ────────────────────────────────────────────────────────


def _load_quiz() -> Quiz:
    path = DEMO_DATA / "quiz.json"
    if not path.exists():
        raise SystemExit(
            f"{path} not found — capture a real quiz first:\n"
            f"  uv run python scripts/capture_demo_quiz.py {PR_URL}"
        )
    return Quiz.model_validate_json(path.read_text())


def _curate_feed() -> list[tuple[dict[str, Any], float]]:
    """Load the captured activity feed and trim it to a short, legible montage.

    Keeps the leading `step`, then a mix of tool calls, thinking, and prose in
    original order up to `MAX_FEED_LINES`, truncating long text. Returns
    (event, delay) pairs. Falls back to a tiny synthetic feed if none was captured.
    """
    path = DEMO_DATA / "feed.json"
    if not path.exists():
        return [({"kind": "step", "tool": "submit_quiz"}, 0.5)]
    events: list[dict[str, Any]] = json.loads(path.read_text())

    curated: list[dict[str, Any]] = []
    for ev in events:
        if len(curated) >= MAX_FEED_LINES:
            break
        kind = ev.get("kind")
        if kind in ("text", "thinking"):
            text = (ev.get("text") or "").strip()
            if not text:
                continue
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS].rstrip() + "…"
            curated.append({**ev, "text": text})
        elif kind in ("step", "tool_use"):
            curated.append(ev)
    return [(ev, _DELAY_BY_KIND.get(ev.get("kind", ""), 0.6)) for ev in curated]


# ── correct-answer helpers (data-driven playback) ───────────────────────


def _answer_question(page: Page, file_loc: Locator, q: Any) -> None:
    """Select the correct answer for one question, whatever its type/position."""
    file_loc.scroll_into_view_if_needed()
    page.wait_for_timeout(550)
    if isinstance(q, MCQQuestion):
        idx = q.options.index(q.answer)
        file_loc.locator(".option").nth(idx).click()
    elif isinstance(q, MermaidQuestion):
        idx = list(q.options).index(q.answer)
        file_loc.locator(".diagram").nth(idx).click()
    elif isinstance(q, TrueFalseQuestion):
        file_loc.locator(".tf__cell").nth(0 if q.answer else 1).click()
    elif isinstance(q, OpenQuestion):
        ta = file_loc.locator("textarea")
        ta.click()
        ta.type(OPEN_ANSWERS.get(q.id, _OPEN_FALLBACK), delay=16)
    page.wait_for_timeout(850)


# ── server / streaming plumbing ─────────────────────────────────────────


def _stream_generation(broker: Any, quiz: Quiz, feed: list[tuple[dict[str, Any], float]]) -> None:
    """Replay the curated activity feed into the broker, then flip it to ready."""
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


def _record_webm(base_url: str, video_dir: Path, quiz: Quiz) -> Path:
    """Drive the browser through the demo and return the recorded .webm path."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            record_video_dir=str(video_dir),
            record_video_size=RECORD_SIZE,
        )
        page = ctx.new_page()

        # --- Generating intro: the live activity feed while Claude "writes" the quiz.
        # Don't wait for networkidle — the page polls /progress continuously here.
        page.goto(base_url, wait_until="load")
        page.wait_for_selector("#term-feed .term__line", timeout=5000)
        page.wait_for_timeout(2500)  # let a few feed lines stream in

        # --- Quiz renders once the broker flips to ready.
        page.wait_for_selector(
            "#questions-root .file .option, #questions-root .diagram", timeout=8000
        )
        page.wait_for_selector("#questions-root .diagram svg", timeout=10000)
        page.wait_for_timeout(1200)

        # --- Answer every question correctly, in order (data-driven).
        files = page.locator("#questions-root .file")
        for i, q in enumerate(quiz.questions):
            _answer_question(page, files.nth(i), q)

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
                "ffmpeg",
                "-y",
                "-i",
                str(webm),
                "-vf",
                f"{vf_common},palettegen=stats_mode=diff",
                str(palette),
            ],
            check=True,
            capture_output=True,
        )
        # Pass 2: apply the palette with dithering; loop forever.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(webm),
                "-i",
                str(palette),
                "-lavfi",
                f"{vf_common} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3",
                "-loop",
                "0",
                str(out),
            ],
            check=True,
            capture_output=True,
        )


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found on PATH — install it to build the GIF.")

    quiz = _load_quiz()
    feed = _curate_feed()
    # Start in the "generating" phase (quiz=None) so the GIF captures the streaming
    # activity feed before the quiz appears. A background thread replays the captured
    # feed and then flips the broker to ready.
    app = build_app(
        quiz=None,
        pr_number=quiz.pr_number,
        pr_url=PR_URL,
        llm=FakeLLM(canned_open_score=88, canned_open_feedback="Captures the key idea."),
        post_comment=lambda body: f"{PR_URL}#issuecomment-9999",
    )
    threading.Thread(
        target=_stream_generation, args=(app.state.broker, quiz, feed), daemon=True
    ).start()

    port = _free_port()
    server = _serve(app, port)
    base = f"http://127.0.0.1:{port}"

    video_dir = Path(tempfile.mkdtemp(prefix="cognit-demo-"))
    try:
        webm = _record_webm(base, video_dir, quiz)
        _webm_to_gif(webm, OUT_PATH)
        size_mb = OUT_PATH.stat().st_size / 1_000_000
        print(f"wrote {OUT_PATH} ({size_mb:.2f} MB)")
    finally:
        server.should_exit = True
        shutil.rmtree(video_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
