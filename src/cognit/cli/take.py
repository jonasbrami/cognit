"""`cognit take` — the only command.

Generates the quiz in memory (cached locally for resume), opens the browser quiz,
grades in-session, opt-in publishes the results comment to the PR. The quiz itself
is **never posted to the PR** — only the results comment, and only when the user
clicks Publish.
"""

import hashlib
import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

import typer
import uvicorn
from pydantic import ValidationError

from cognit.comment.parse import parse_results
from cognit.engine.generate import generate_quiz
from cognit.engine.llm import LLMClient
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import Quiz
from cognit.ghio.diff import fetch_pr_diff
from cognit.ghio.pr import fetch_pr_info, find_latest_marker_comment, post_comment
from cognit.server.app import build_app

logger = logging.getLogger("cognit.cli.take")

_MARKER_RESULTS = "<!-- cognit:results v1 -->"


def _make_llm(model: str) -> LLMClient:
    """The `claude` binary (subprocessed by claude_agent_sdk) is the only inference
    path: it's what unlocks sonnet/opus for Claude Code OAuth users. Run `claude login`."""
    return ClaudeAgentLLM(model=model)


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


def _cache_path_for(pr_url: str) -> Path:
    """Local cache path for a generated quiz, keyed by PR URL digest.

    Lives under `$TMPDIR/cognit/`. OS reboot clears it. No explicit lifecycle.
    """
    digest = hashlib.sha1(pr_url.encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(tempfile.gettempdir()) / "cognit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.json"


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


def _generate_in_memory(
    pr_url: str,
    llm: LLMClient,
    model: str,
    min_diff_lines: int,
    max_diff_lines: int,
) -> Quiz | None:
    """Generate a quiz from the PR's diff. Returns the Quiz, or None if skipped.

    Unlike the previous behaviour, does NOT post anything to the PR — the quiz
    lives only in memory (and the local cache `_load_or_generate` writes).
    """
    info = fetch_pr_info(pr_url)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return None
    # Cheap size gate only — the diff is discarded here. The outline agent re-fetches it
    # via its `pr_diff` tool; we keep this so a too-small/too-large PR is skipped before
    # paying for an LLM call. `fetch_pr_diff` already strips vendored/minified/lock files,
    # so the line count reflects the real change.
    diff_lines = fetch_pr_diff(pr_url).count("\n")
    if diff_lines < min_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (< {min_diff_lines}) — skipping.")
        return None
    if diff_lines > max_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (> {max_diff_lines}) — skipping.")
        return None
    try:
        return generate_quiz(
            pr_title=info.title,
            pr_body=info.body,
            pr_number=info.number,
            pr_url=pr_url,
            branch=info.branch,
            llm=llm,
            model=model,
        )
    except ValidationError as e:
        typer.echo(f"LLM returned malformed quiz: {e}", err=True)
        raise typer.Exit(code=1) from None
    except RuntimeError as e:
        typer.echo(f"LLM call failed: {e}", err=True)
        raise typer.Exit(code=1) from None


def _load_or_generate(
    pr_url: str,
    llm: LLMClient,
    model: str,
    min_diff_lines: int,
    max_diff_lines: int,
) -> Quiz | None:
    """Return a Quiz: from local cache if present, else generate fresh and cache it."""
    cache_path = _cache_path_for(pr_url)
    if cache_path.exists():
        logger.debug("cache hit: loading quiz from %s", cache_path)
        try:
            return Quiz.model_validate_json(cache_path.read_text())
        except ValidationError:
            logger.debug("cache at %s is invalid — regenerating", cache_path)
            cache_path.unlink(missing_ok=True)
    logger.debug("cache miss: will generate fresh quiz (will write to %s)", cache_path)
    typer.echo("generating quiz from diff...")
    quiz = _generate_in_memory(pr_url, llm, model, min_diff_lines, max_diff_lines)
    if quiz is None:
        return None
    cache_path.write_text(quiz.model_dump_json())
    logger.debug("quiz cached at %s (%d bytes)", cache_path, cache_path.stat().st_size)
    return quiz


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

    quiz = _load_or_generate(
        pr_url,
        llm=llm,
        model=model,
        min_diff_lines=min_diff_lines,
        max_diff_lines=max_diff_lines,
    )
    if quiz is None:
        return
    _serve_blocking(
        quiz,
        pr_url,
        llm=llm,
        post_comment_fn=lambda md: post_comment(pr_url, md),
    )


def _configure_logging() -> None:
    """Wire up `COGNIT_LOG_LEVEL` so debug traces from the engine layers are visible.

    Default is WARNING (quiet). Set `COGNIT_LOG_LEVEL=DEBUG` to see which mermaid
    validator is being used, cache hits, and other internal decisions:

        COGNIT_LOG_LEVEL=DEBUG cognit take

    `force=True` so a parent harness (uvicorn, pytest) that already configured
    root logging doesn't make our env var silently a no-op.
    """
    level_name = os.environ.get("COGNIT_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(name)s %(levelname)s: %(message)s",
        force=True,
    )


def run(
    pr: str | None,
    show_results: bool,
    model: str = "claude-sonnet-4-6",
    min_diff_lines: int = 50,
    max_diff_lines: int = 2000,
) -> None:
    _configure_logging()
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
