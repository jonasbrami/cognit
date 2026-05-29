"""FastAPI app: the browser projection over QuizState.

Endpoints:
  GET  /state         — JSON {quiz, answers, confidences, results}; the browser polls this
  POST /answer        — {question_id, value} → record a browser-side answer
  POST /confidence    — {question_id, value:1-5} → record the reader's confidence rating
  GET  /diff          — ?path= → the unified-diff section for one changed file (inline hunks)
  GET  /changed-files — JSON {files:[...]} for the diff coverage map
  POST /grade         — human-triggered "Submit quiz": grade now (handler-owned, same path
                        the agent's `grade` tool uses) and store results. Returns the Results.
  POST /publish       — human-gated: render + post the results scorecard comment (reuses
                        ghio.pr.post_comment). The ONLY outward-facing action; never an agent tool.
  GET  /              — the quiz page (polls /state)
  GET  /static/*      — bundled assets
"""

from __future__ import annotations

import html as _html
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from cognit.comment.render import render_results_inlined
from cognit.engine.models import AnswerEntry, Answers, Results
from cognit.mcp.state import QuizState

logger = logging.getLogger("cognit.mcp.web")

_ASSETS_DIR = Path(__file__).parent / "assets"


def build_web_app(
    state: QuizState,
    *,
    post_comment: Callable[[str], str],
    grade: Callable[[], Results] | None = None,
    diff_section: Callable[[str], str] | None = None,
    changed_files: Callable[[], list[str]] | None = None,
    pr_url: str = "",
    branch: str = "",
) -> FastAPI:
    """Browser projection over `state`.

    `grade` (when provided) is invoked by POST /grade — the human-clicked "Submit quiz"
    button. It runs the same handler-owned grading the agent's `grade` tool uses and
    must store the result in `state`; we return the Results to the page. `diff_section`
    (when provided) returns the unified-diff section for one changed file — GET /diff
    serves it so the browser can show a question's anchored hunk inline. `changed_files`
    (when provided) lists the PR's changed-file paths — GET /changed-files serves it so
    the browser can render the diff coverage map. `pr_url` feeds the page chrome (the
    "on GitHub" links).
    """
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(_ASSETS_DIR)), name="static")
    pr_url_attr = _html.escape(pr_url, quote=True)
    branch_attr = _html.escape(branch, quote=True)
    index_html = (
        (_ASSETS_DIR / "index.html")
        .read_text()
        .replace("__PR__", str(state.pr_number))
        .replace("__PR_URL_ATTR__", pr_url_attr)
        .replace("__BRANCH_ATTR__", branch_attr)
    )

    @app.get("/state")
    def get_state() -> JSONResponse:
        return JSONResponse(state.snapshot())

    @app.post("/answer")
    async def post_answer(req: Request) -> JSONResponse:
        body = await req.json()
        qid, value = body.get("question_id"), body.get("value")
        if not isinstance(qid, str) or not isinstance(value, str):
            return JSONResponse(
                {"ok": False, "error": "question_id and value (strings) required"},
                status_code=422,
            )
        state.record_answer(qid, value)
        return JSONResponse({"ok": True})

    @app.post("/confidence")
    async def post_confidence(req: Request) -> JSONResponse:
        body = await req.json()
        qid, value = body.get("question_id"), body.get("value")
        # bool is an int subclass — reject it explicitly so True/False can't sneak through.
        if not isinstance(qid, str) or isinstance(value, bool) or not isinstance(value, int):
            return JSONResponse(
                {"ok": False, "error": "question_id (string) and value (int) required"},
                status_code=422,
            )
        if not (1 <= value <= 5):
            return JSONResponse({"ok": False, "error": "value must be 1–5"}, status_code=422)
        state.record_confidence(qid, value)
        return JSONResponse({"ok": True})

    @app.get("/diff", response_class=PlainTextResponse)
    def get_diff(path: str = "") -> PlainTextResponse:
        if diff_section is None:
            return PlainTextResponse("diff not available", status_code=503)
        return PlainTextResponse(diff_section(path))

    @app.get("/changed-files")
    def get_changed_files() -> JSONResponse:
        if changed_files is None:
            return JSONResponse({"error": "diff not available"}, status_code=503)
        return JSONResponse({"files": changed_files()})

    @app.post("/grade")
    async def do_grade() -> JSONResponse:
        if grade is None:
            return JSONResponse({"ok": False, "error": "grading not available"}, status_code=501)
        try:
            # grade() is sync and (for ClaudeAgentLLM) drives its own event loop — offload
            # off the request loop so it doesn't collide with uvicorn's.
            results = await run_in_threadpool(grade)
        except RuntimeError as e:  # e.g. no quiz to grade
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        return JSONResponse(results.model_dump(mode="json"))

    @app.post("/publish")
    def publish() -> JSONResponse:
        snap = state.publishable()
        if snap is None:
            return JSONResponse(
                {"ok": False, "error": "nothing graded to publish"}, status_code=409
            )
        quiz, answers_map, results = snap
        answers = Answers(
            pr_number=state.pr_number,
            entries=[AnswerEntry(question_id=q, value=v) for q, v in answers_map.items()],
        )
        # Posting goes through `gh` (subprocess + network), so it can fail transiently
        # (rate limits, blips, auth). Surface the real reason to the browser and log it
        # rather than letting it bubble into a bare, undiagnosable 500.
        try:
            url = post_comment(render_results_inlined(quiz, answers, results))
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip() or f"gh exited {e.returncode}"
            logger.exception("publish failed: posting the PR comment errored")
            return JSONResponse(
                {"ok": False, "error": f"posting the comment failed — {detail}"},
                status_code=502,
            )
        except Exception as e:  # noqa: BLE001 — never a silent 500; report what broke
            logger.exception("publish failed")
            return JSONResponse({"ok": False, "error": f"publish failed — {e}"}, status_code=502)
        return JSONResponse({"ok": True, "total_score": results.total_score, "comment_url": url})

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(index_html)

    return app
