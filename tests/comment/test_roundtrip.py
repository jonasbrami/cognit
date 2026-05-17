from quizz.comment.render import render_quiz, render_answers, render_results
from quizz.comment.parse import parse_quiz, parse_answers, parse_results
from quizz.engine.models import (
    Quiz, Answers, Results, AnswerEntry, QuestionResult,
    MCQQuestion, OpenQuestion,
)


def _sample_quiz() -> Quiz:
    return Quiz(pr_number=7, questions=[
        MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="B"),
        OpenQuestion(id="q2", prompt="?", rubric="r"),
    ])


def test_quiz_roundtrip():
    q = _sample_quiz()
    assert parse_quiz(render_quiz(q)) == q


def test_quiz_parse_finds_block_amid_user_edits():
    q = _sample_quiz()
    md = "Some prefix\n" + render_quiz(q) + "\n\nUser appended text"
    assert parse_quiz(md) == q


def test_answers_roundtrip():
    a = Answers(pr_number=7, entries=[AnswerEntry(question_id="q1", value="B")])
    assert parse_answers(render_answers(a, 100)) == a


def test_results_roundtrip():
    r = Results(pr_number=7, total_score=85,
                per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")])
    md = render_results(r)
    parsed = parse_results(md)
    assert parsed.total_score == 85
    assert parsed.per_question[0].question_id == "q1"
