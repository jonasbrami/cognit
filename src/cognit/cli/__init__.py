import typer
from cognit.cli.version import __version__
from cognit.cli import take as _take

app = typer.Typer(no_args_is_help=True, help="PR-author comprehension quiz tool")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cognit {__version__}")
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
    model: str = typer.Option("claude-sonnet-4-6", "--model"),
) -> None:
    """Take a quiz on a PR. Generates one if none exists, opens browser, grades in-session, optional publish."""
    _take.run(
        pr,
        show_results=show_results,
        model=model,
    )
