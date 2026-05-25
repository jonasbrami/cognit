from cognit.engine.llm_fake import FakeLLM


def test_fake_grades_open_question() -> None:
    llm = FakeLLM(canned_open_score=75, canned_open_feedback="ok")
    score, fb = llm.grade_open(
        question_prompt="why?",
        rubric="r",
        answer="because",
    )
    assert score == 75
    assert fb == "ok"
