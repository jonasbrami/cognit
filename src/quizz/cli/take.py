"""`quizz take` — user-facing command. Opens a browser quiz over the PR's quiz comment."""

import json
import socket
import subprocess
import threading
import webbrowser
from collections.abc import Callable

import typer
import uvicorn

from quizz.comment.parse import parse_quiz, parse_results
from quizz.engine.llm import LLMClient
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.engine.models import Quiz
from quizz.ghio.pr import find_latest_marker_comment, post_comment
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


def _run_take_flow(pr_url: str, show_results_only: bool, llm: LLMClient) -> None:
    if show_results_only:
        results_md = find_latest_marker_comment(pr_url, _MARKER_RESULTS)
        if results_md is None:
            typer.echo("no results comment found on this PR.")
            raise typer.Exit(code=1)
        typer.echo(parse_results(results_md).model_dump_json(indent=2))
        return

    quiz_md = find_latest_marker_comment(pr_url, _MARKER_QUIZ)
    if quiz_md is None:
        typer.echo("no quiz comment found on this PR — run `quizz generate --pr ... --post` first.")
        raise typer.Exit(code=1)
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
) -> None:
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)
    llm = _make_llm(model)
    _run_take_flow(pr_url, show_results_only=show_results, llm=llm)
