"""Local FastAPI app for `quizz take`."""

import html as _html
import json as _json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from quizz.comment.render import render_results_inlined
from quizz.engine.grade import grade
from quizz.engine.llm import LLMClient
from quizz.engine.models import Answers, Quiz, Results

# Assets directory: use __file__-relative path so StaticFiles gets a real directory.
# importlib.resources returns a MultiplexedPath in editable installs that os.path.isdir rejects.
_ASSETS_DIR: Path = Path(__file__).parent / "assets"


def _assets_dir() -> Path:
    """Path to the embedded assets directory (works for installed wheels)."""
    return _ASSETS_DIR


def build_app(
    *,
    quiz: Quiz,
    pr_url: str,
    llm: LLMClient,
    post_comment: Callable[[str], str],  # returns the comment's html_url
) -> FastAPI:
    """Build the FastAPI app for `quizz take`.

    Endpoints:
      GET /          — quiz HTML page
      GET /static/*  — bundled assets (CSS, JS, mermaid.min.js)
      POST /submit   — grade everything in-session (deterministic + LLM open Q); returns
                       the full Results to the browser. Does NOT post any comment.
      POST /publish  — opt-in: post the results comment to the PR.

    The browser shows the result inline and only publishes when the user clicks the
    Publish button.
    """
    app = FastAPI()
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
        # Escape </ so a question prompt containing </script> can't close the inline script tag.
        safe_quiz_json = quiz.model_dump_json().replace("</", "<\\/")
        # pr_url needs two escaped forms:
        #   __PR_URL_ATTR__ → for href="..." attributes (HTML-escaped)
        #   __PR_URL_JS__   → for window.PR_URL = ...; (JSON-encoded JS string literal incl. quotes)
        pr_url_attr = _html.escape(pr_url, quote=True)
        # json.dumps quotes the string and backslash-escapes internal quotes.
        # We also replace any bare < with < so the HTML parser never sees
        # a <script> or </script> tag sequence inside the inline script block.
        pr_url_js = _json.dumps(pr_url).replace("<", "\\u003c")
        html = (
            index_template.replace("__PR__", str(quiz.pr_number))
            .replace("__PR_URL_ATTR__", pr_url_attr)
            .replace("__PR_URL_JS__", pr_url_js)
            .replace("__QUIZ_JSON__", safe_quiz_json)
        )
        return HTMLResponse(html)

    @app.post("/submit")
    async def submit(req: Request) -> JSONResponse:
        body = await req.json()
        answers = Answers.model_validate(body)
        last_answers["current"] = answers
        # Grade EVERYTHING in-session: deterministic (MCQ/mermaid/T/F) + LLM for open Q.
        results = grade(quiz, answers, llm=llm)
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
        comment_url = post_comment(render_results_inlined(quiz, answers, results))
        return JSONResponse(
            {"ok": True, "total_score": results.total_score, "comment_url": comment_url}
        )

    return app
