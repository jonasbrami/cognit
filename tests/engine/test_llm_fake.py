from cognit.engine.llm import GenerateRequest, LLMClient
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, QuizDraft


def test_fake_returns_canned_draft() -> None:
    canned = QuizDraft(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    llm: LLMClient = FakeLLM(canned_draft=canned)
    out = llm.draft_quiz(
        GenerateRequest(
            pr_title="t", pr_body="b", pr_number=1, pr_url="https://x/pull/1", branch="br"
        )
    )
    assert out == canned


def test_fake_grades_open_question() -> None:
    llm = FakeLLM(canned_open_score=75, canned_open_feedback="ok")
    score, fb = llm.grade_open(
        question_prompt="why?",
        rubric="r",
        answer="because",
    )
    assert score == 75
    assert fb == "ok"
