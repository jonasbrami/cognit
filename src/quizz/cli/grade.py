"""`quizz grade` — LLM-grade open question + post results comment to PR."""

import typer
from anthropic import APIError as AnthropicAPIError
from pydantic import ValidationError

from quizz.comment.parse import parse_answers, parse_quiz
from quizz.comment.render import render_results
from quizz.engine.grade import grade
from quizz.engine.llm import LLMClient
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.ghio.pr import find_latest_marker_comment, post_comment


def _make_llm(model: str) -> LLMClient:
    """Construct the Anthropic LLM client. Kept as a function for test monkeypatch points."""
    return AnthropicLLM(model=model)


def run(pr: str, model: str = "claude-sonnet-4-6") -> None:
    quiz_md = find_latest_marker_comment(pr, "<!-- quizz:quiz v1 -->")
    answers_md = find_latest_marker_comment(pr, "<!-- quizz:answers v1 -->")
    if not (quiz_md and answers_md):
        typer.echo("missing quiz or answers comment — nothing to grade.")
        return
    quiz = parse_quiz(quiz_md)
    answers = parse_answers(answers_md)
    try:
        results = grade(quiz, answers, llm=_make_llm(model))
    except AnthropicAPIError as e:
        typer.echo(f"LLM grading failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from None
    except ValidationError as e:
        typer.echo(f"LLM grading failed: {e}", err=True)
        raise typer.Exit(code=1) from None
    post_comment(pr, render_results(results))
    typer.echo(f"results posted: total {results.total_score}%")
