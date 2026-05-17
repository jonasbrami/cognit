from typer.testing import CliRunner
from quizz.cli import app

runner = CliRunner()


def test_take_errors_when_no_pr(monkeypatch):
    monkeypatch.setattr("quizz.cli.take._detect_pr_from_branch", lambda: None)
    result = runner.invoke(app, ["take"])
    assert result.exit_code != 0
    assert "no pr" in result.stdout.lower()


def test_take_auto_detects(monkeypatch):
    monkeypatch.setattr(
        "quizz.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/42",
    )
    captured = {}
    monkeypatch.setattr(
        "quizz.cli.take._run_take_flow",
        lambda pr_url, show_results_only: captured.update(
            {"pr": pr_url, "show": show_results_only}
        ),
    )
    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0, result.stdout
    assert captured["pr"] == "https://github.com/o/r/pull/42"
    assert captured["show"] is False
