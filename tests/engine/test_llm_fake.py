from quizz.engine.llm import LLMClient, GenerateRequest
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import Quiz, MCQQuestion


def test_fake_returns_canned_quiz():
    canned = Quiz(
        pr_number=1,
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    llm: LLMClient = FakeLLM(canned_quiz=canned)
    out = llm.generate_quiz(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))
    assert out == canned


def test_fake_grades_open_question():
    llm = FakeLLM(canned_open_score=75, canned_open_feedback="ok")
    score, fb = llm.grade_open(
        question_prompt="why?",
        rubric="r",
        answer="because",
    )
    assert score == 75
    assert fb == "ok"
