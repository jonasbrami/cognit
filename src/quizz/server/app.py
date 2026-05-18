"""Local FastAPI app for `quizz take`."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from quizz.comment.render import render_results
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

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (
            index_template.replace("__PR__", str(quiz.pr_number))
            .replace("__PR_URL__", pr_url)
            .replace("__QUIZ_JSON__", quiz.model_dump_json())
        )
        return HTMLResponse(html)

    @app.post("/submit")
    async def submit(req: Request) -> JSONResponse:
        body = await req.json()
        answers = Answers.model_validate(body)
        # Grade EVERYTHING in-session: deterministic (MCQ/mermaid/T/F) + LLM for open Q.
        results = grade(quiz, answers, llm=llm)
        return JSONResponse(results.model_dump())

    @app.post("/publish")
    async def publish(req: Request) -> JSONResponse:
        body = await req.json()
        results = Results.model_validate(body)
        comment_url = post_comment(render_results(results))
        return JSONResponse(
            {"ok": True, "total_score": results.total_score, "comment_url": comment_url}
        )

    return app
