"""`cognit take` — the only command.

Launches Claude Code as the quiz host in a confined MCP session: generates the
quiz, opens the browser UI, grades answers, and optionally publishes results to the
PR. The quiz itself is **never posted to the PR** — only the results comment, and
only when the user clicks Publish.
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import hashlib
import tempfile
from pathlib import Path

import typer

from cognit.comment.parse import parse_results
from cognit.ghio.pr import fetch_pr_info, find_latest_marker_comment
from cognit.mcp.launch import build_launch_spec

logger = logging.getLogger("cognit.cli.take")

_MARKER_RESULTS = "<!-- cognit:results v1 -->"


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
    # When debug logging is on, persist claude's own debug stream to a file alongside the
    # config. The MCP server's stderr (verbose at this level) is captured separately by
    # claude under ~/.cache/claude-cli-nodejs/.../mcp-logs-cognit/.
    debug = os.environ.get("COGNIT_LOG_LEVEL", "").upper() == "DEBUG"
    debug_log = cfg_dir / f"{digest}-claude-debug.log" if debug else None
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
        debug_log=debug_log,
    )
    mcp_cfg.write_text(spec.mcp_config_json)
    settings.write_text(spec.settings_json)
    if debug_log is not None:
        typer.echo(f"cognit: debug logging on — claude debug log → {debug_log}")
    if resume:
        typer.echo(
            f"cognit: resuming existing quiz for PR #{info.number} (browser opens shortly)…"
        )
    else:
        typer.echo(
            f"cognit: launching quiz session for PR #{info.number} (browser opens shortly)…"
        )
    os.execvpe("claude", spec.argv, {**os.environ, **spec.env})
