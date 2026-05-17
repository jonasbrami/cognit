from quizz.engine.llm import LLMClient, GenerateRequest
from quizz.engine.models import Quiz, MermaidQuestion, Question
from quizz.engine.mermaid import is_valid_mermaid


def _validate_mermaid(source: str) -> bool:
    return is_valid_mermaid(source, strict=False)


def generate_quiz(
    *,
    diff: str,
    pr_title: str,
    pr_body: str,
    files: dict[str, str],
    pr_number: int,
    llm: LLMClient,
    max_mermaid_retries: int = 2,
    model: str = "gpt-4o-mini",
) -> Quiz:
    req = GenerateRequest(
        diff=diff,
        pr_title=pr_title,
        pr_body=pr_body,
        files=files,
        model=model,
    )
    quiz = llm.generate_quiz(req)
    quiz = Quiz(version="1", pr_number=pr_number, questions=quiz.questions)

    for attempt in range(max_mermaid_retries + 1):
        bad = [
            q
            for q in quiz.questions
            if isinstance(q, MermaidQuestion)
            and not all(_validate_mermaid(src) for src in q.options.values())
        ]
        if not bad:
            return quiz
        if attempt < max_mermaid_retries:
            retried = llm.generate_quiz(req)
            quiz = Quiz(version="1", pr_number=pr_number, questions=retried.questions)

    # Last resort: drop mermaid questions
    kept: list[Question] = [q for q in quiz.questions if not isinstance(q, MermaidQuestion)]
    return Quiz(version="1", pr_number=pr_number, questions=kept)
