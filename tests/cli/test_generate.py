from typer.testing import CliRunner
from quizz.cli import app
from quizz.engine.models import Quiz, MCQQuestion
from quizz.engine.llm_fake import FakeLLM
from quizz.ghio.pr import PRInfo

runner = CliRunner()


def test_generate_dry_run_prints_markdown(monkeypatch):
    canned = Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A"),
        ],
    )
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.generate.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: (
            "diffstr\n" * 100,
            {},
        ),  # 100 lines, above default min
    )
    monkeypatch.setattr(
        "quizz.cli.generate._make_llm",
        lambda model: FakeLLM(canned_quiz=canned),
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
