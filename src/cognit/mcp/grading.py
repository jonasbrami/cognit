"""Grade the answers currently held in QuizState. Reuses engine.grade.grade (deterministic
for mcq/tf/mermaid; the existing strict single-shot grade_open for open questions), so
calibration is identical to today. The agent triggers this but supplies no judgments."""

from __future__ import annotations

from cognit.engine.grade import grade
from cognit.engine.llm import LLMClient
from cognit.engine.models import AnswerEntry, Answers, Results
from cognit.mcp.state import QuizState


def grade_state(state: QuizState, *, llm: LLMClient) -> Results:
    if state.quiz is None:
        raise RuntimeError("no quiz to grade")
    answers = Answers(
        pr_number=state.pr_number,
        entries=[AnswerEntry(question_id=qid, value=val) for qid, val in state.answers.items()],
    )
    results = grade(state.quiz, answers, llm=llm)
    state.set_results(results)
    return results
