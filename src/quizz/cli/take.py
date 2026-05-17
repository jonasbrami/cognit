"""`quizz take` — user-facing command."""

import json
import subprocess
import typer


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


def _run_take_flow(pr_url: str, show_results_only: bool) -> None:
    """Fetch quiz, serve browser UI, post answers. Implemented in M5.4."""
    raise NotImplementedError("filled in M5.4")


def run(pr: str | None, show_results: bool) -> None:
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)
    _run_take_flow(pr_url, show_results_only=show_results)
