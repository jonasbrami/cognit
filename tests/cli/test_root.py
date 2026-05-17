from typer.testing import CliRunner
from quizz.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "quizz" in result.stdout.lower()


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert "take" in result.stdout
    assert "generate" in result.stdout
    assert "grade" in result.stdout
