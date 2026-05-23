"""Local FastAPI app for `cognit take`."""

import asyncio
import html as _html
import json as _json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cognit.comment.render import render_results_inlined
from cognit.engine.grade import grade
from cognit.engine.llm import LLMClient
from cognit.engine.models import Answers, Quiz, Results
from cognit.server.streaming import Broker

# Assets directory: use __file__-relative path so StaticFiles gets a real directory.
# importlib.resources returns a MultiplexedPath in editable installs that os.path.isdir rejects.
_ASSETS_DIR: Path = Path(__file__).parent / "assets"


def _assets_dir() -> Path:
    """Path to the embedded assets directory (works for installed wheels)."""
    return _ASSETS_DIR


def build_app(
    *,
    quiz: Quiz | None = None,
    pr_url: str,
    llm: LLMClient,
    post_comment: Callable[[str], str],  # returns the comment's html_url
    pr_number: int | None = None,
) -> FastAPI:
    """Build the FastAPI app for `cognit take`.

    `quiz` may be None: on a cache miss the server starts *before* generation and
    the browser shows a live activity feed (phase "generating"), polling /progress
    until the worker thread flips the broker to "ready" with the finished quiz.
    A ready quiz (cache hit) renders immediately, exactly as before. `pr_number`
    is only needed for the chrome when there's no quiz yet to read it from.

    Endpoints:
      GET /          — quiz HTML page (or the generating shell when quiz is None)
      GET /static/*  — bundled assets (CSS, JS, mermaid.min.js)
      GET /progress  — JSON snapshot of the activity feed + phase/quiz/error (polled)
      POST /submit   — grade everything in-session (deterministic + LLM open Q); returns
                       the full Results to the browser. Does NOT post any comment.
      POST /publish  — opt-in: post the results comment to the PR.

    The broker is exposed on `app.state.broker` so the CLI can feed it from the
    background generation thread.
    """
    app = FastAPI()
    broker = Broker(quiz=quiz)
    app.state.broker = broker
    # PR number for the page chrome: from the quiz when we have one, else the
    # explicitly-passed number (generating mode has no quiz to read it from).
    display_pr = pr_number if pr_number is not None else (quiz.pr_number if quiz else 0)
    assets = _assets_dir()
    app.mount("/static", StaticFiles(directory=str(assets)), name="static")
    index_template = (assets / "index.html").read_text()
    # Hold the most recently submitted Answers so /publish can render the results
    # comment with question prompts + author answers inlined. (The PR thread no
    # longer carries a quiz comment to cross-reference, so the results comment is
    # self-contained.)
    last_answers: dict[str, Answers] = {}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # When generation is still running there's no quiz yet — inject `null` and
        # let the frontend boot into the polling "generating" view. Read from the
        # broker (not the closure) so a refresh after generation finishes serves
        # the ready quiz directly.
        if broker.quiz is not None:
            # Escape </ so a question prompt containing </script> can't close the inline script tag.
            safe_quiz_json = broker.quiz.model_dump_json().replace("</", "<\\/")
        else:
            safe_quiz_json = "null"
        # pr_url needs two escaped forms:
        #   __PR_URL_ATTR__ → for href="..." attributes (HTML-escaped)
        #   __PR_URL_JS__   → for window.PR_URL = ...; (JSON-encoded JS string literal incl. quotes)
        pr_url_attr = _html.escape(pr_url, quote=True)
        # json.dumps quotes the string and backslash-escapes internal quotes.
        # We also replace any bare < with < so the HTML parser never sees
        # a <script> or </script> tag sequence inside the inline script block.
        pr_url_js = _json.dumps(pr_url).replace("<", "\\u003c")
        html = (
            index_template.replace("__PR__", str(display_pr))
            .replace("__PR_URL_ATTR__", pr_url_attr)
            .replace("__PR_URL_JS__", pr_url_js)
            .replace("__PHASE__", broker.phase)
            .replace("__QUIZ_JSON__", safe_quiz_json)
        )
        return HTMLResponse(html)

    @app.get("/progress")
    def progress(cursor: int = 0) -> JSONResponse:
        """Poll target for the activity feed. Returns events from `cursor` onward
        plus the current phase/quiz/error. Cheap and stateless — each client
        carries its own cursor, so refresh/multi-tab/reconnect all replay."""
        return JSONResponse(broker.snapshot(cursor))

    @app.post("/submit")
    async def submit(req: Request) -> JSONResponse:
        body = await req.json()
        answers = Answers.model_validate(body)
        last_answers["current"] = answers
        quiz = broker.quiz
        if quiz is None:
            # Shouldn't happen — the UI only enables submit once the quiz renders.
            return JSONResponse({"error": "quiz not ready"}, status_code=409)
        # Stream grading activity (open-question LLM calls) into the same feed the
        # browser is polling. No-op for AnthropicLLM, which never reads on_event.
        setattr(llm, "on_event", broker.emit)
        # Grade EVERYTHING in-session: deterministic (MCQ/mermaid/T/F) + LLM for open Q.
        # `grade()` is sync and (for ClaudeAgentLLM) internally calls
        # `asyncio.run(...)` — which Python forbids from inside a running event
        # loop. Offload to a worker thread so the adapter can drive its own loop
        # without colliding with uvicorn's. As a bonus this also keeps
        # AnthropicLLM's blocking HTTP off the event-loop thread.
        results = await asyncio.to_thread(grade, quiz, answers, llm=llm)
        return JSONResponse(results.model_dump())

    @app.post("/publish")
    async def publish(req: Request) -> JSONResponse:
        body = await req.json()
        results = Results.model_validate(body)
        answers = last_answers.get("current")
        if answers is None:
            return JSONResponse(
                {"ok": False, "error": "no submission to publish; call /submit first"},
                status_code=400,
            )
        quiz = broker.quiz
        if quiz is None:
            return JSONResponse(
                {"ok": False, "error": "quiz not ready"}, status_code=409
            )
        comment_url = post_comment(render_results_inlined(quiz, answers, results))
        return JSONResponse(
            {"ok": True, "total_score": results.total_score, "comment_url": comment_url}
        )

    return app
