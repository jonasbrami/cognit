import pytest
import typer
from typer.testing import CliRunner

from quizz.cli import app
from quizz.engine.llm_fake import FakeLLM

runner = CliRunner()


def _fake_llm() -> FakeLLM:
    return FakeLLM(canned_open_score=80, canned_open_feedback="ok")


def test_take_errors_when_no_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("quizz.cli.take._detect_pr_from_branch", lambda: None)
    result = runner.invoke(app, ["take"])
    assert result.exit_code != 0
    assert "no pr" in result.stdout.lower()


def test_take_auto_detects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "quizz.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/42",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "quizz.cli.take._run_take_flow",
        lambda pr_url, show_results_only, llm: captured.update(
            {"pr": pr_url, "show": show_results_only, "llm": llm}
        ),
    )
    monkeypatch.setattr("quizz.cli.take._make_llm", lambda model, provider: _fake_llm())
    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0, result.stdout
    assert captured["pr"] == "https://github.com/o/r/pull/42"
    assert captured["show"] is False


def test_take_flow_fetches_parses_and_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """When invoked, the flow finds the quiz comment, parses it, and hands off to the server."""
    from quizz.comment.render import render_quiz
    from quizz.engine.models import MCQQuestion, Quiz

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
    captured: dict[str, object] = {}

    def fake_serve(quiz_, pr_url, llm, post_comment_fn):  # type: ignore[no-untyped-def]
        captured["quiz"] = quiz_
        captured["pr_url"] = pr_url
        captured["llm"] = llm
        captured["post_comment_fn"] = post_comment_fn

    monkeypatch.setattr("quizz.cli.take._serve_blocking", fake_serve)

    from quizz.cli.take import _run_take_flow

    _run_take_flow("https://github.com/o/r/pull/42", show_results_only=False, llm=_fake_llm())

    assert captured["quiz"] == quiz
    assert captured["pr_url"] == "https://github.com/o/r/pull/42"
    assert callable(captured["post_comment_fn"])


def test_take_show_results_when_no_results_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow("https://github.com/o/r/pull/42", show_results_only=True, llm=_fake_llm())
    assert exc_info.value.exit_code == 1


def test_take_no_quiz_comment_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow("https://github.com/o/r/pull/42", show_results_only=False, llm=_fake_llm())
    assert exc_info.value.exit_code == 1
