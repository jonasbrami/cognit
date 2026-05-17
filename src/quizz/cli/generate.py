"""`quizz generate` — used by the Generator GitHub Action."""
import typer

from quizz.comment.render import render_quiz
from quizz.engine.generate import generate_quiz
from quizz.engine.llm import LLMClient
from quizz.engine.llm_githubmodels import GitHubModelsLLM
from quizz.ghio.diff import fetch_diff_and_files, read_file_at_head
from quizz.ghio.pr import fetch_pr_info, post_comment


def _make_llm(model: str) -> LLMClient:
    """Factory hook — monkeypatched in tests to inject FakeLLM."""
    return GitHubModelsLLM()


def run(
    pr: str,
    post: bool = False,
    dry_run: bool = False,
    model: str = "gpt-4o-mini",
    min_diff_lines: int = 50,
    max_diff_lines: int = 2000,
) -> None:
    info = fetch_pr_info(pr)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return
    diff, files = fetch_diff_and_files(pr, fetch_file_contents=read_file_at_head)
    diff_lines = diff.count("\n")
    if diff_lines < min_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (< {min_diff_lines}) — skipping.")
        return
    if diff_lines > max_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (> {max_diff_lines}) — skipping.")
        return
    quiz = generate_quiz(
        diff=diff,
        pr_title=info.title,
        pr_body=info.body,
        files=files,
        pr_number=info.number,
        llm=_make_llm(model),
    )
    md = render_quiz(quiz)
    if dry_run:
        typer.echo(md)
        return
    if post:
        post_comment(pr, md)
        typer.echo("quiz comment posted.")
    else:
        typer.echo(md)
