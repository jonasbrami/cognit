from cognit.engine.llm import LLMClient
from cognit.engine.models import (
    Quiz,
    Answers,
    Results,
    QuestionResult,
    MCQQuestion,
    MermaidQuestion,
    OpenQuestion,
    TrueFalseQuestion,
)


def grade(quiz: Quiz, answers: Answers, *, llm: LLMClient) -> Results:
    by_id = {e.question_id: e.value for e in answers.entries}
    per: list[QuestionResult] = []
    for q in quiz.questions:
        v = by_id.get(q.id, "")
        if isinstance(q, (MCQQuestion, MermaidQuestion)):
            ok = v == q.answer
            per.append(
                QuestionResult(
                    question_id=q.id,
                    correct=ok,
                    score=100 if ok else 0,
                    feedback="",
                )
            )
        elif isinstance(q, TrueFalseQuestion):
            ok = v.strip().lower() == ("true" if q.answer else "false")
            per.append(
                QuestionResult(
                    question_id=q.id,
                    correct=ok,
                    score=100 if ok else 0,
                    feedback="",
                )
            )
        elif isinstance(q, OpenQuestion):
            score, fb = llm.grade_open(q.prompt, q.rubric, v)
            # Align with the calibrated bands in prompts/system_grade.txt: 75+ is the
            # "most rubric items addressed" tier. A 72 is in the 50-74 partial band and
            # should NOT render as a green check in the UI.
            per.append(
                QuestionResult(
                    question_id=q.id,
                    correct=score >= 75,
                    score=score,
                    feedback=fb,
                )
            )
    total = sum(r.score for r in per) // len(per) if per else 0
    return Results(pr_number=quiz.pr_number, total_score=total, per_question=per)
