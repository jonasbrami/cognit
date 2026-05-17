import typer
from quizz.cli.version import __version__

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


@app.command()
def take() -> None:
    """Take a quiz on a PR."""
    typer.echo("take: not implemented yet")


@app.command()
def generate() -> None:
    """Generate a quiz on a PR (used by the GitHub Action)."""
    typer.echo("generate: not implemented yet")


@app.command()
def grade() -> None:
    """Grade submitted answers (used by the GitHub Action)."""
    typer.echo("grade: not implemented yet")
