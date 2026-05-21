"""`quizz take` — the only command. Generates a quiz on the PR if none exists,
opens the browser quiz, grades in-session, optional publish."""

import json
import socket
import subprocess
import threading
import webbrowser
from collections.abc import Callable

import typer
import uvicorn
from anthropic import APIError as AnthropicAPIError
from pydantic import ValidationError

from quizz.comment.parse import parse_quiz, parse_results
from quizz.comment.render import render_quiz
from quizz.engine.generate import generate_quiz
from quizz.engine.llm import LLMClient
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.engine.models import Quiz
from quizz.ghio.diff import fetch_diff_and_files, read_file_at_head
from quizz.ghio.pr import fetch_pr_info, find_latest_marker_comment, post_comment
from quizz.server.app import build_app

_MARKER_QUIZ = "<!-- quizz:quiz v1 -->"
_MARKER_RESULTS = "<!-- quizz:results v1 -->"


def _make_llm(model: str) -> LLMClient:
    """Construct the Anthropic LLM client. Kept as a function for test monkeypatch points."""
    return AnthropicLLM(model=model)


def _detect_pr_from_branch() -> str | None:
    """Use `gh pr view` to find the PR for the current branch."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "url"],
            capture_output=True,
            text=True,
            check=True,
        )
        return str(json.loads(result.stdout)["url"])
    except subprocess.CalledProcessError:
        return None


def _free_port() -> int:
    """Find an unused localhost TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _serve_blocking(
    quiz: Quiz,
    pr_url: str,
    llm: LLMClient,
    post_comment_fn: Callable[[str], str],
) -> None:
    """Build the FastAPI app, launch the browser, run uvicorn until killed."""
    app = build_app(quiz=quiz, pr_url=pr_url, llm=llm, post_comment=post_comment_fn)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"opening {url} in your browser... (Ctrl-C to quit)")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _generate_and_post(
    pr_url: str,
    llm: LLMClient,
    model: str,
    min_diff_lines: int,
    max_diff_lines: int,
) -> str | None:
    """Generate a quiz from the PR's diff and post it as a PR comment.

    Returns the rendered markdown of the posted comment, or None if the PR was
    skipped (`quiz: skip` in body, diff smaller than min, diff larger than max).
    """
    info = fetch_pr_info(pr_url)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return None
    diff, files = fetch_diff_and_files(pr_url, fetch_file_contents=read_file_at_head)
    diff_lines = diff.count("\n")
    if diff_lines < min_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (< {min_diff_lines}) — skipping.")
        return None
    if diff_lines > max_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (> {max_diff_lines}) — skipping.")
        return None
    try:
        quiz = generate_quiz(
            diff=diff,
            pr_title=info.title,
            pr_body=info.body,
            files=files,
            pr_number=info.number,
            llm=llm,
            model=model,
        )
    except AnthropicAPIError as e:
        typer.echo(f"LLM call failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from None
    except ValidationError as e:
        typer.echo(f"LLM returned malformed quiz: {e}", err=True)
        raise typer.Exit(code=1) from None
    md = render_quiz(quiz)
    post_comment(pr_url, md)
    typer.echo("quiz comment posted to PR.")
    return md


def _run_take_flow(
    pr_url: str,
    show_results_only: bool,
    llm: LLMClient,
    model: str = "claude-sonnet-4-6",
    min_diff_lines: int = 50,
    max_diff_lines: int = 2000,
) -> None:
    if show_results_only:
        results_md = find_latest_marker_comment(pr_url, _MARKER_RESULTS)
        if results_md is None:
            typer.echo("no results comment found on this PR.")
            raise typer.Exit(code=1)
        typer.echo(parse_results(results_md).model_dump_json(indent=2))
        return

    quiz_md = find_latest_marker_comment(pr_url, _MARKER_QUIZ)
    if quiz_md is None:
        typer.echo("no quiz on this PR yet — generating one...")
        quiz_md = _generate_and_post(
            pr_url,
            llm=llm,
            model=model,
            min_diff_lines=min_diff_lines,
            max_diff_lines=max_diff_lines,
        )
        if quiz_md is None:
            return
    quiz = parse_quiz(quiz_md)
    _serve_blocking(
        quiz,
        pr_url,
        llm=llm,
        post_comment_fn=lambda md: post_comment(pr_url, md),
    )


def run(
    pr: str | None,
    show_results: bool,
    model: str = "claude-sonnet-4-6",
    min_diff_lines: int = 50,
    max_diff_lines: int = 2000,
) -> None:
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)
    llm = _make_llm(model)
    _run_take_flow(
        pr_url,
        show_results_only=show_results,
        llm=llm,
        model=model,
        min_diff_lines=min_diff_lines,
        max_diff_lines=max_diff_lines,
    )
