import httpx
import pytest
import typer
from anthropic import APIError as AnthropicAPIError
from typer.testing import CliRunner

from quizz.cli import app
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import MCQQuestion, QuizOutline
from quizz.ghio.pr import PRInfo

runner = CliRunner()


def _fake_llm() -> FakeLLM:
    return FakeLLM(canned_open_score=80, canned_open_feedback="ok")


def _fake_llm_with_outline() -> FakeLLM:
    return FakeLLM(
        canned_outline=QuizOutline(
            questions=[MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A")],
        ),
        canned_open_score=80,
        canned_open_feedback="ok",
    )


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
        lambda pr_url, show_results_only, llm, **kw: captured.update(
            {"pr": pr_url, "show": show_results_only, "llm": llm, **kw}
        ),
    )
    monkeypatch.setattr("quizz.cli.take._make_llm", lambda model: _fake_llm())
    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0, result.stdout
    assert captured["pr"] == "https://github.com/o/r/pull/42"
    assert captured["show"] is False


def test_take_flow_fetches_parses_and_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a quiz comment already exists, use it as-is and hand off to the server."""
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


def test_take_auto_generates_when_no_quiz_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no quiz comment exists on the PR, take generates one, posts it, then serves it."""
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    monkeypatch.setattr(
        "quizz.cli.take.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("diffstr\n" * 100, {}),
    )
    posted: dict[str, str] = {}
    monkeypatch.setattr(
        "quizz.cli.take.post_comment",
        lambda pr, md: posted.update({"pr": pr, "md": md}) or "https://github.com/o/r/pull/42#c1",
    )
    served: dict[str, object] = {}

    def fake_serve(quiz_, pr_url, llm, post_comment_fn):  # type: ignore[no-untyped-def]
        served["quiz"] = quiz_
        served["pr_url"] = pr_url

    monkeypatch.setattr("quizz.cli.take._serve_blocking", fake_serve)

    _run_take_flow(
        "https://github.com/o/r/pull/42",
        show_results_only=False,
        llm=_fake_llm_with_outline(),
    )

    assert "<!-- quizz:quiz v1 -->" in posted["md"]
    assert "why?" in posted["md"]
    assert served["pr_url"] == "https://github.com/o/r/pull/42"


def test_take_skips_small_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    monkeypatch.setattr(
        "quizz.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("only one line\n", {}),
    )

    def fail_serve(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("should not serve when diff is too small")

    monkeypatch.setattr("quizz.cli.take._serve_blocking", fail_serve)

    _run_take_flow(
        "https://github.com/o/r/pull/1", show_results_only=False, llm=_fake_llm_with_outline()
    )


def test_take_respects_quiz_skip_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from quizz.cli.take import _run_take_flow

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    monkeypatch.setattr(
        "quizz.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "quiz: skip\n\nThis PR ...", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )

    def fail_serve(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("should not serve when quiz: skip is in body")

    monkeypatch.setattr("quizz.cli.take._serve_blocking", fail_serve)

    _run_take_flow(
        "https://github.com/o/r/pull/1", show_results_only=False, llm=_fake_llm_with_outline()
    )


def test_take_handles_llm_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM failure during auto-generation should exit 1 with a friendly message."""
    from quizz.cli.take import _run_take_flow

    class BoomLLM:
        def generate_quiz_outline(self, req):  # type: ignore[no-untyped-def]
            raise AnthropicAPIError(
                message="simulated network failure",
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                body=None,
            )

        def generate_mermaid_set(self, spec, req):  # type: ignore[no-untyped-def]
            raise AssertionError("should not be reached")

        def grade_open(self, *args):  # type: ignore[no-untyped-def]
            return (0, "")

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    monkeypatch.setattr(
        "quizz.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow(
            "https://github.com/o/r/pull/1",
            show_results_only=False,
            llm=BoomLLM(),  # type: ignore[arg-type]
        )
    assert exc_info.value.exit_code == 1
