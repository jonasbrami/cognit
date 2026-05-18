import httpx
from anthropic import APIError as AnthropicAPIError
from typer.testing import CliRunner

from quizz.cli import app
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import MCQQuestion, QuizOutline
from quizz.ghio.pr import PRInfo

runner = CliRunner()


def _outline_with_one_mcq() -> QuizOutline:
    return QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A")],
    )


def test_generate_dry_run_prints_markdown(monkeypatch):
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("diffstr\n" * 100, {}),
    )
    monkeypatch.setattr(
        "quizz.cli.generate._make_llm",
        lambda model: FakeLLM(canned_outline=_outline_with_one_mcq()),
    )
    result = runner.invoke(app, ["generate", "--pr", "https://github.com/o/r/pull/42", "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "<!-- quizz:quiz v1 -->" in result.stdout
    assert "why?" in result.stdout


def test_generate_skips_small_diff(monkeypatch):
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("only one line\n", {}),
    )
    result = runner.invoke(app, ["generate", "--pr", "https://github.com/o/r/pull/1", "--dry-run"])
    assert result.exit_code == 0
    assert "skipping" in result.stdout.lower()


def test_generate_respects_quiz_skip_in_body(monkeypatch):
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "quiz: skip\n\nThis PR ...", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )
    result = runner.invoke(app, ["generate", "--pr", "https://github.com/o/r/pull/1", "--dry-run"])
    assert result.exit_code == 0
    assert "skip" in result.stdout.lower()


def test_generate_handles_llm_failure(monkeypatch):
    class BoomLLM:
        def generate_quiz_outline(self, req):
            raise AnthropicAPIError(
                message="simulated network failure",
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                body=None,
            )

        def generate_mermaid_set(self, spec, req):
            raise AssertionError("should not be reached")

        def grade_open(self, *args):
            return (0, "")

    monkeypatch.setattr(
        "quizz.cli.generate.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )
    monkeypatch.setattr("quizz.cli.generate._make_llm", lambda model: BoomLLM())
    result = runner.invoke(app, ["generate", "--pr", "https://github.com/o/r/pull/1", "--dry-run"])
    assert result.exit_code == 1
