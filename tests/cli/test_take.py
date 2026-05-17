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


def test_take_flow_fetches_parses_and_runs_server(monkeypatch):
    """When invoked, the flow finds the quiz comment, parses it, and hands off to the server."""
    from quizz.engine.models import Quiz, MCQQuestion
    from quizz.comment.render import render_quiz

    quiz = Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A"),
        ],
    )
    monkeypatch.setattr(
        "quizz.cli.take.find_latest_marker_comment",
        lambda pr, marker: render_quiz(quiz) if "quiz" in marker else None,
    )
    captured = {}

    def fake_serve(quiz_, pr_url, post_answers):
        captured["quiz"] = quiz_
        captured["pr_url"] = pr_url
        captured["post_answers"] = post_answers

    monkeypatch.setattr("quizz.cli.take._serve_blocking", fake_serve)

    from quizz.cli.take import _run_take_flow

    _run_take_flow("https://github.com/o/r/pull/42", show_results_only=False)

    assert captured["quiz"] == quiz
    assert captured["pr_url"] == "https://github.com/o/r/pull/42"
    # post_answers is a callable that wraps post_comment for the right PR
    assert callable(captured["post_answers"])


def test_take_show_results_when_no_results_yet(monkeypatch):
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    import pytest
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow("https://github.com/o/r/pull/42", show_results_only=True)
    assert exc_info.value.exit_code == 1


def test_take_no_quiz_comment_errors(monkeypatch):
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    import pytest
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow("https://github.com/o/r/pull/42", show_results_only=False)
    assert exc_info.value.exit_code == 1
