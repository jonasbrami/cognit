import typer
from quizz.cli.version import __version__
from quizz.cli import take as _take

app = typer.Typer(no_args_is_help=True, help="PR-author comprehension quiz tool")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"quizz {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    pass


@app.command("take")
def take_cmd(
    pr: str | None = typer.Option(None, "--pr", help="PR URL (default: auto-detect)"),
    show_results: bool = typer.Option(False, "--show-results"),
) -> None:
    """Take a quiz on a PR locally."""
    _take.run(pr, show_results=show_results)


@app.command("generate")
def generate_cmd(
    pr: str = typer.Option(..., "--pr", help="PR URL or number"),
    post: bool = typer.Option(False, "--post", help="Post the quiz as a PR comment"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    model: str = typer.Option("gpt-4o-mini", "--model"),
    min_diff_lines: int = typer.Option(50, "--min-diff-lines"),
    max_diff_lines: int = typer.Option(2000, "--max-diff-lines"),
) -> None:
    """Generate a quiz on a PR (used by the GitHub Action)."""
    import quizz.cli.generate as _gen

    _gen.run(
        pr,
        post=post,
        dry_run=dry_run,
        model=model,
        min_diff_lines=min_diff_lines,
        max_diff_lines=max_diff_lines,
    )


@app.command()
def grade() -> None:
    """Grade submitted answers (used by the GitHub Action)."""
    typer.echo("grade: not implemented yet")
