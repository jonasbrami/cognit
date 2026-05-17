"""Local FastAPI app for `quizz take`."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from quizz.comment.render import render_answers
from quizz.engine.grade import grade
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import Answers, Quiz

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
    post_answers: Callable[[str], None],
) -> FastAPI:
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
        # Deterministic grading immediately. Open Q gets 0 score and "awaiting CI" feedback;
        # the grader Action will produce a real score later via the results comment.
        results = grade(
            quiz, answers, llm=FakeLLM(canned_open_score=0, canned_open_feedback="awaiting CI")
        )
        # Compute deterministic-only score (skip open questions)
        non_open = [
            r
            for r in results.per_question
            if any(q.id == r.question_id and q.type != "open" for q in quiz.questions)
        ]
        det_score = (sum(r.score for r in non_open) // len(non_open)) if non_open else 0
        md = render_answers(answers, deterministic_score=det_score)
        post_answers(md)
        return JSONResponse(
            {
                "deterministic_score": det_score,
                "per_question": [r.model_dump() for r in results.per_question],
            }
        )

    return app
