import httpx
from anthropic import APIError as AnthropicAPIError
from typer.testing import CliRunner

from quizz.cli import app
from quizz.comment.render import render_answers, render_quiz
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import AnswerEntry, Answers, MCQQuestion, OpenQuestion, Quiz

runner = CliRunner()


def test_grade_command_posts_results(monkeypatch):
    quiz = Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A"),
            OpenQuestion(id="q2", prompt="?", rubric="r"),
        ],
    )
    answers = Answers(
        pr_number=42,
        entries=[
            AnswerEntry(question_id="q1", value="A"),
            AnswerEntry(question_id="q2", value="my answer"),
        ],
    )
    monkeypatch.setattr(
        "quizz.cli.grade.find_latest_marker_comment",
        lambda pr, marker: (
            render_quiz(quiz) if "answers" not in marker else render_answers(answers, 100)
        ),
    )
    posted: list[str] = []
    monkeypatch.setattr("quizz.cli.grade.post_comment", lambda pr, md: posted.append(md))
    monkeypatch.setattr(
        "quizz.cli.grade._make_llm",
        lambda model: FakeLLM(canned_open_score=85, canned_open_feedback="solid"),
    )
    result = runner.invoke(app, ["grade", "--pr", "https://github.com/o/r/pull/42"])
    assert result.exit_code == 0, result.stdout
    assert posted, "expected a results comment to be posted"
    assert "<!-- quizz:results v1 -->" in posted[0]


def test_grade_skips_when_no_quiz_or_answers(monkeypatch):
    monkeypatch.setattr("quizz.cli.grade.find_latest_marker_comment", lambda pr, marker: None)
    posted: list[str] = []
    monkeypatch.setattr("quizz.cli.grade.post_comment", lambda pr, md: posted.append(md))
    result = runner.invoke(app, ["grade", "--pr", "https://github.com/o/r/pull/1"])
    assert result.exit_code == 0
    assert not posted
    assert "nothing to grade" in result.stdout.lower() or "missing" in result.stdout.lower()


def test_grade_handles_llm_failure(monkeypatch):
    quiz = Quiz(
        pr_number=42,
        questions=[
            OpenQuestion(id="q1", prompt="?", rubric="r"),
        ],
    )
    answers = Answers(
        pr_number=42,
        entries=[AnswerEntry(question_id="q1", value="my answer")],
    )
    monkeypatch.setattr(
        "quizz.cli.grade.find_latest_marker_comment",
        lambda pr, marker: (
            render_quiz(quiz) if "answers" not in marker else render_answers(answers, 0)
        ),
    )
    monkeypatch.setattr("quizz.cli.grade.post_comment", lambda pr, md: None)

    class BoomLLM:
        def generate_quiz_outline(self, req):
            raise AssertionError("grade should not generate")

        def generate_mermaid_set(self, spec, req):
            raise AssertionError("grade should not render mermaid")

        def grade_open(self, *args):
            raise AnthropicAPIError(
                message="simulated grading failure",
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                body=None,
            )

    monkeypatch.setattr("quizz.cli.grade._make_llm", lambda model: BoomLLM())
    result = runner.invoke(app, ["grade", "--pr", "https://github.com/o/r/pull/42"])
    assert result.exit_code == 1
