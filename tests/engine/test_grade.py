from quizz.engine.grade import grade
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import (
    Quiz, MCQQuestion, OpenQuestion, TrueFalseQuestion,
    Answers, AnswerEntry,
)


def _quiz_with_one_of_each() -> Quiz:
    return Quiz(
        pr_number=1,
        questions=[
            MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="B"),
            OpenQuestion(id="q2", prompt="?", rubric="r"),
            TrueFalseQuestion(id="q3", prompt="?", answer=True),
        ],
    )


def test_deterministic_correct():
    quiz = _quiz_with_one_of_each()
    ans = Answers(pr_number=1, entries=[
        AnswerEntry(question_id="q1", value="B"),
        AnswerEntry(question_id="q2", value="long answer"),
        AnswerEntry(question_id="q3", value="true"),
    ])
    res = grade(quiz, ans, llm=FakeLLM(canned_open_score=80, canned_open_feedback="ok"))
    by = {r.question_id: r for r in res.per_question}
    assert by["q1"].correct and by["q1"].score == 100
    assert by["q2"].score == 80 and by["q2"].feedback == "ok"
    assert by["q3"].correct
    # total = (100 + 80 + 100) / 3 = 93
    assert res.total_score == 93


def test_deterministic_wrong():
    quiz = _quiz_with_one_of_each()
    ans = Answers(pr_number=1, entries=[
        AnswerEntry(question_id="q1", value="A"),
        AnswerEntry(question_id="q2", value=""),
        AnswerEntry(question_id="q3", value="false"),
    ])
    res = grade(quiz, ans, llm=FakeLLM(canned_open_score=10, canned_open_feedback="no"))
    by = {r.question_id: r for r in res.per_question}
    assert not by["q1"].correct and by["q1"].score == 0
    assert by["q2"].score == 10
    assert not by["q3"].correct
