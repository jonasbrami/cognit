"""`quizz grade` — used by the Grader GitHub Action."""

import os

import typer
from openai import OpenAIError
from pydantic import ValidationError

from quizz.comment.parse import parse_answers, parse_quiz
from quizz.comment.render import render_results
from quizz.engine.grade import grade
from quizz.engine.llm import LLMClient
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.engine.llm_githubmodels import GitHubModelsLLM
from quizz.ghio.pr import find_latest_marker_comment, post_comment


def _make_llm(model: str, provider: str = "auto") -> LLMClient:
    """Pick an LLM provider. 'auto' uses Anthropic if ANTHROPIC_API_KEY is set, else GitHub Models."""
    if provider == "auto":
        provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "github"
    if provider == "anthropic":
        anthropic_model = model if model not in ("gpt-4o-mini", "gpt-4o") else "claude-sonnet-4-6"
        return AnthropicLLM(model=anthropic_model)
    return GitHubModelsLLM(model=model)


def run(pr: str, model: str = "gpt-4o-mini", provider: str = "auto") -> None:
    quiz_md = find_latest_marker_comment(pr, "<!-- quizz:quiz v1 -->")
    answers_md = find_latest_marker_comment(pr, "<!-- quizz:answers v1 -->")
    if not (quiz_md and answers_md):
        typer.echo("missing quiz or answers comment — nothing to grade.")
        return
    quiz = parse_quiz(quiz_md)
    answers = parse_answers(answers_md)
    try:
        results = grade(quiz, answers, llm=_make_llm(model, provider))
    except OpenAIError as e:
        typer.echo(f"LLM grading failed: {e}", err=True)
        raise typer.Exit(code=1)
    except ValidationError as e:
        typer.echo(f"LLM grading failed: {e}", err=True)
        raise typer.Exit(code=1)
    post_comment(pr, render_results(results))
    typer.echo(f"results posted: total {results.total_score}%")
