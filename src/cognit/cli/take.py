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
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer
import uvicorn
from pydantic import ValidationError

from cognit.comment.parse import parse_results
from cognit.engine.generate import generate_quiz
from cognit.engine.llm import LLMClient
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import Quiz
from cognit.ghio.pr import fetch_pr_info, find_latest_marker_comment, post_comment
from cognit.mcp.launch import build_launch_spec
from cognit.server.app import build_app
from cognit.server.streaming import Broker

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


def _open_browser_when_ready(url: str, port: int) -> None:
    """Open the browser once the server accepts connections.

    The page now hits /progress immediately, so opening before uvicorn is
    listening would flash a connection error. Poll the port (best-effort, ~5s)
    then open.
    """
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    webbrowser.open(url)


def _serve_blocking(
    quiz: Quiz | None,
    pr_url: str,
    llm: LLMClient,
    post_comment_fn: Callable[[str], str],
    pr_number: int | None = None,
    on_generate: Callable[[Broker], None] | None = None,
) -> None:
    """Build the FastAPI app, launch the browser, run uvicorn until killed.

    On a cache miss `quiz` is None and `on_generate` is provided: generation runs
    on a daemon thread that feeds the app's broker, while the browser shows the
    live activity feed and polls /progress until the quiz is ready.
    """
    app = build_app(
        quiz=quiz, pr_url=pr_url, llm=llm, post_comment=post_comment_fn, pr_number=pr_number
    )
    if on_generate is not None:
        broker: Broker = app.state.broker
        threading.Thread(target=on_generate, args=(broker,), daemon=True).start()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"opening {url} in your browser... (Ctrl-C to quit)")
    threading.Thread(target=_open_browser_when_ready, args=(url, port), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@dataclass
class _GenPrep:
    """Everything `generate_quiz` needs, gathered by the synchronous pre-flight.

    No diff/files — the outline agent fetches the diff itself via its `pr_diff`
    tool and reads the working tree, so we only carry PR metadata."""

    pr_title: str
    pr_body: str
    pr_number: int
    branch: str


def _prepare_generation(pr_url: str) -> _GenPrep | None:
    """Fetch PR info and apply the `quiz: skip` opt-out. Runs synchronously
    *before* the server starts, so a skipped PR exits cleanly and never opens a
    browser. Returns the bundle to generate from, or None if the PR is skipped.

    No diff is fetched here — the outline agent re-fetches it itself via its
    `pr_diff` tool, so we only need PR metadata."""
    info = fetch_pr_info(pr_url)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return None
    return _GenPrep(
        pr_title=info.title,
        pr_body=info.body,
        pr_number=info.number,
        branch=info.branch,
    )


def _write_transcript(pr_url: str, pr_number: int, broker: Broker) -> None:
    """If COGNIT_TRANSCRIPT_DIR is set, dump the run's activity feed (Claude's
    thinking, tool calls + args, validation events, final phase) to a JSON file for
    offline analysis/optimization. Opt-in so normal runs stay side-effect-free; a
    write failure is logged and swallowed so it can never break a generation."""
    dir_env = os.environ.get("COGNIT_TRANSCRIPT_DIR")
    if not dir_env:
        return
    try:
        out_dir = Path(dir_env).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        snap = broker.snapshot(0)
        path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-pr{pr_number}.json"
        path.write_text(
            json.dumps(
                {
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "phase": snap["phase"],
                    "error": snap.get("error"),
                    "events": snap["events"],
                },
                indent=2,
            )
        )
        typer.echo(f"transcript written to {path}", err=True)
    except Exception as e:  # never let transcript logging break a run
        logger.debug("transcript write failed: %s", e)


def _run_generation(
    broker: Broker,
    *,
    prep: _GenPrep,
    pr_url: str,
    llm: LLMClient,
    model: str,
) -> None:
    """Background worker: generate the quiz, stream activity to the broker, cache
    on success. A failure flips the broker to phase=error (shown in the browser)
    rather than crashing — the server stays up so the user can read the message.
    """
    setattr(llm, "on_event", broker.emit)
    try:
        quiz = generate_quiz(
            pr_title=prep.pr_title,
            pr_body=prep.pr_body,
            pr_number=prep.pr_number,
            pr_url=pr_url,
            branch=prep.branch,
            llm=llm,
            model=model,
        )
    except Exception as e:
        # Terminal background thread: its whole job is to report failure to the
        # browser. Catch broadly — besides ValidationError/RuntimeError, the agent's
        # `pr_diff` tool can raise subprocess.CalledProcessError (gh failure), which
        # must flip the broker to `error` rather than escaping and leaving the page
        # polling `generating` forever.
        typer.echo(f"quiz generation failed: {e}", err=True)
        broker.set_error(str(e))
        _write_transcript(pr_url, prep.pr_number, broker)
        return
    cache_path = _cache_path_for(pr_url)
    cache_path.write_text(quiz.model_dump_json())
    logger.debug("quiz cached at %s (%d bytes)", cache_path, cache_path.stat().st_size)
    broker.set_ready(quiz)
    _write_transcript(pr_url, prep.pr_number, broker)


def _load_cached(pr_url: str) -> Quiz | None:
    """Return the cached quiz for this PR, or None on miss/corrupt cache."""
    cache_path = _cache_path_for(pr_url)
    if cache_path.exists():
        logger.debug("cache hit: loading quiz from %s", cache_path)
        try:
            return Quiz.model_validate_json(cache_path.read_text())
        except ValidationError:
            logger.debug("cache at %s is invalid — regenerating", cache_path)
            cache_path.unlink(missing_ok=True)
    return None


def _run_take_flow(
    pr_url: str,
    show_results_only: bool,
    llm: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> None:
    if show_results_only:
        results_md = find_latest_marker_comment(pr_url, _MARKER_RESULTS)
        if results_md is None:
            typer.echo("no results comment found on this PR.")
            raise typer.Exit(code=1)
        typer.echo(parse_results(results_md).model_dump_json(indent=2))
        return

    def post_comment_fn(md: str) -> str:
        return post_comment(pr_url, md)

    # Cache hit: serve the ready quiz immediately, exactly as before.
    cached = _load_cached(pr_url)
    if cached is not None:
        _serve_blocking(
            cached, pr_url, llm=llm, post_comment_fn=post_comment_fn, pr_number=cached.pr_number
        )
        return

    # Cache miss: gather + gate synchronously, then serve a "generating" page and
    # generate in the background, streaming Claude's activity to the browser.
    prep = _prepare_generation(pr_url)
    if prep is None:
        return
    typer.echo("generating quiz...")

    def on_generate(broker: Broker) -> None:
        _run_generation(broker, prep=prep, pr_url=pr_url, llm=llm, model=model)

    _serve_blocking(
        None,
        pr_url,
        llm=llm,
        post_comment_fn=post_comment_fn,
        pr_number=prep.pr_number,
        on_generate=on_generate,
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


def _load_host_prompt() -> str:
    from importlib import resources

    return resources.files("cognit.engine.prompts").joinpath("system_generate.txt").read_text()


def run(
    pr: str | None,
    show_results: bool,
    model: str = "claude-sonnet-4-6",
) -> None:
    _configure_logging()
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)

    # --show-results: print the latest results comment and exit (no launch).
    if show_results:
        results_md = find_latest_marker_comment(pr_url, _MARKER_RESULTS)
        if results_md is None:
            typer.echo("no results comment found on this PR.")
            raise typer.Exit(code=1)
        typer.echo(parse_results(results_md).model_dump_json(indent=2))
        return

    if shutil.which("claude") is None:
        typer.echo("error: `claude` not found. Install Claude Code and run `claude login`.")
        raise typer.Exit(code=1)

    try:
        info = fetch_pr_info(pr_url)
    except subprocess.CalledProcessError:
        typer.echo(f"error: could not fetch PR info for {pr_url} (is `gh` installed and authenticated?)")
        raise typer.Exit(code=1)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return

    try:
        repo_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except subprocess.CalledProcessError:
        typer.echo("error: not inside a git repository.")
        raise typer.Exit(code=1)
    port = _free_port()
    snapshot = _cache_path_for(pr_url)
    resume = False
    if snapshot.exists():
        try:
            resume = json.loads(snapshot.read_text()).get("quiz") is not None
        except (json.JSONDecodeError, OSError):
            resume = False
    cfg_dir = snapshot.parent / "launch"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    digest = snapshot.stem
    mcp_cfg, settings = cfg_dir / f"{digest}-mcp.json", cfg_dir / f"{digest}-settings.json"
    spec = build_launch_spec(
        pr_url=pr_url,
        pr_number=info.number,
        branch=info.branch,
        port=port,
        snapshot_path=snapshot,
        repo_root=repo_root,
        mcp_config_path=mcp_cfg,
        settings_path=settings,
        system_prompt=_load_host_prompt(),
        model=model,
        resume=resume,
    )
    mcp_cfg.write_text(spec.mcp_config_json)
    settings.write_text(spec.settings_json)
    if resume:
        typer.echo(
            f"cognit: resuming existing quiz for PR #{info.number} (browser opens shortly)…"
        )
    else:
        typer.echo(
            f"cognit: launching quiz session for PR #{info.number} (browser opens shortly)…"
        )
    os.execvpe("claude", spec.argv, {**os.environ, **spec.env})
