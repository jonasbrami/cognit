from pathlib import Path

import pytest

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, OpenQuestion, Quiz
from cognit.mcp.grading import grade_state
from cognit.mcp.state import QuizState


def test_grade_state_scores_and_stores(tmp_path: Path) -> None:
    quiz = Quiz(
        pr_number=7,
        questions=[
            MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A", explanation="x"),
            OpenQuestion(id="q2", prompt="why?", rubric="mentions X"),
        ],
    )
    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    state.set_quiz(quiz)
    state.record_answer("q1", "A")  # correct
    state.record_answer("q2", "some prose")
    results = grade_state(state, llm=FakeLLM(canned_open_score=80, canned_open_feedback="ok"))
    assert results.per_question[0].correct is True
    assert results.per_question[1].score == 80
    assert state.results is not None and state.results.total_score == results.total_score


def test_grade_state_raises_without_quiz(tmp_path: Path) -> None:
    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    with pytest.raises(RuntimeError, match="no quiz"):
        grade_state(state, llm=FakeLLM())
