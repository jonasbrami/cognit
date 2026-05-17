from quizz.engine.llm import LLMClient
from quizz.engine.models import (
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
            per.append(
                QuestionResult(
                    question_id=q.id,
                    correct=score >= 70,
                    score=score,
                    feedback=fb,
                )
            )
    total = sum(r.score for r in per) // len(per) if per else 0
    return Results(pr_number=quiz.pr_number, total_score=total, per_question=per)
