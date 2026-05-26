"""FastAPI app: the browser projection over QuizState.

Endpoints:
  GET  /state    — JSON {quiz, answers, results}; the browser polls this
  POST /answer   — {question_id, value} → record a browser-side answer
  POST /grade    — human-triggered "Submit quiz": grade now (handler-owned, same path
                   the agent's `grade` tool uses) and store results. Returns the Results.
  POST /publish  — human-gated: render + post the results scorecard comment (reuses
                   ghio.pr.post_comment). The ONLY outward-facing action; never an agent tool.
  GET  /         — the quiz page (polls /state)
  GET  /static/* — bundled assets
"""

from __future__ import annotations

import html as _html
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cognit.comment.render import render_results_inlined
from cognit.engine.models import AnswerEntry, Answers, Results
from cognit.mcp.state import QuizState

_ASSETS_DIR = Path(__file__).parent / "assets"


def build_web_app(
    state: QuizState,
    *,
    post_comment: Callable[[str], str],
    grade: Callable[[], Results] | None = None,
    pr_url: str = "",
) -> FastAPI:
    """Browser projection over `state`.

    `grade` (when provided) is invoked by POST /grade — the human-clicked "Submit quiz"
    button. It runs the same handler-owned grading the agent's `grade` tool uses and
    must store the result in `state`; we return the Results to the page. `pr_url` feeds
    the page chrome (the "on GitHub" links).
    """
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(_ASSETS_DIR)), name="static")
    pr_url_attr = _html.escape(pr_url, quote=True)
    index_html = (
        (_ASSETS_DIR / "index.html").read_text()
        .replace("__PR__", str(state.pr_number))
        .replace("__PR_URL_ATTR__", pr_url_attr)
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
            return JSONResponse({"ok": False, "error": "nothing graded to publish"}, status_code=409)
        quiz, answers_map, results = snap
        answers = Answers(
            pr_number=state.pr_number,
            entries=[AnswerEntry(question_id=q, value=v) for q, v in answers_map.items()],
        )
        url = post_comment(render_results_inlined(quiz, answers, results))
        return JSONResponse({"ok": True, "total_score": results.total_score, "comment_url": url})

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(index_html)

    return app
