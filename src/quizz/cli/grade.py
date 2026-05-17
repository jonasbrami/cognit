"""`quizz grade` — used by the Grader GitHub Action."""

import os
from pathlib import Path

import typer
from anthropic import APIError as AnthropicAPIError
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
    """Pick an LLM provider. 'auto' prefers Anthropic (API key or Claude Code OAuth), else GitHub Models."""
    if provider == "auto":
        has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_claude_oauth = (Path.home() / ".claude" / ".credentials.json").exists()
        provider = "anthropic" if (has_anthropic_key or has_claude_oauth) else "github"
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
    except (OpenAIError, AnthropicAPIError) as e:
        typer.echo(f"LLM grading failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from None
    except ValidationError as e:
        typer.echo(f"LLM grading failed: {e}", err=True)
        raise typer.Exit(code=1) from None
    post_comment(pr, render_results(results))
    typer.echo(f"results posted: total {results.total_score}%")
