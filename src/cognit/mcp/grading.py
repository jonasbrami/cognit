"""Grade the answers currently held in QuizState. Reuses engine.grade.grade (deterministic
for mcq/tf/mermaid; the existing strict single-shot grade_open for open questions), so
calibration is identical to today. The agent triggers this but supplies no judgments."""

from __future__ import annotations

from cognit.engine.grade import grade
from cognit.engine.llm import LLMClient
from cognit.engine.models import AnswerEntry, Answers, Results
from cognit.mcp.state import QuizState


def grade_state(state: QuizState, *, llm: LLMClient) -> Results:
    snap = state.snapshot_for_grading()
    if snap is None:
        raise RuntimeError("no quiz to grade")
    quiz, answers_map = snap
    answers = Answers(
        pr_number=state.pr_number,
        entries=[AnswerEntry(question_id=qid, value=val) for qid, val in answers_map.items()],
    )
    results = grade(quiz, answers, llm=llm)
    # Attach the reader's self-reported confidence (1–5) to each result so the agent can
    # see confidence vs. correctness — e.g. to follow up on confident-but-wrong questions.
    confidences = dict(state.confidences)
    if confidences:
        results = results.model_copy(
            update={
                "per_question": [
                    r.model_copy(update={"confidence": confidences.get(r.question_id)})
                    for r in results.per_question
                ]
            }
        )
    state.set_results(results)
    return results
