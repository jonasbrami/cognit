"""`quizz grade` — used by the Grader GitHub Action."""

import typer

from quizz.comment.parse import parse_answers, parse_quiz
from quizz.comment.render import render_results
from quizz.engine.grade import grade
from quizz.engine.llm import LLMClient
from quizz.engine.llm_githubmodels import GitHubModelsLLM
from quizz.ghio.pr import find_latest_marker_comment, post_comment


def _make_llm(model: str) -> LLMClient:
    """Factory hook — monkeypatched in tests."""
    return GitHubModelsLLM()


def run(pr: str, model: str = "gpt-4o-mini") -> None:
    quiz_md = find_latest_marker_comment(pr, "<!-- quizz:quiz v1 -->")
    answers_md = find_latest_marker_comment(pr, "<!-- quizz:answers v1 -->")
    if not (quiz_md and answers_md):
        typer.echo("missing quiz or answers comment — nothing to grade.")
        return
    quiz = parse_quiz(quiz_md)
    answers = parse_answers(answers_md)
    results = grade(quiz, answers, llm=_make_llm(model))
    post_comment(pr, render_results(results))
    typer.echo(f"results posted: total {results.total_score}%")
