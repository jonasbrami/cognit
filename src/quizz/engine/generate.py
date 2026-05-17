import random

from quizz.engine.llm import LLMClient, GenerateRequest
from quizz.engine.models import Quiz, MermaidQuestion, Question
from quizz.engine.mermaid import is_valid_mermaid


def _validate_mermaid(source: str) -> bool:
    return is_valid_mermaid(source, strict=False)


def _neutralize_mermaid_labels(quiz: Quiz, rng: random.Random | None = None) -> Quiz:
    """Rewrite each MermaidQuestion's option keys to neutral A/B/C/D and shuffle order.

    LLMs occasionally label options with semantic names like "correct" / "wrong_1",
    leaking the answer right in the rendered markdown. This post-processor relabels
    them to neutral letters regardless of what the LLM chose.
    """
    if rng is None:
        rng = random.Random()
    new_questions: list[Question] = []
    for q in quiz.questions:
        if not isinstance(q, MermaidQuestion):
            new_questions.append(q)
            continue
        items = list(q.options.items())  # [(orig_label, src), ...]
        rng.shuffle(items)
        letters = ["A", "B", "C", "D", "E", "F"][: len(items)]
        new_options = {letters[i]: items[i][1] for i in range(len(items))}
        # Find the new label for the original `answer` key
        new_answer = next(
            (letters[i] for i, (orig, _) in enumerate(items) if orig == q.answer),
            letters[0],
        )
        new_questions.append(
            MermaidQuestion(
                id=q.id,
                prompt=q.prompt,
                options=new_options,
                answer=new_answer,
            )
        )
    return Quiz(version="1", pr_number=quiz.pr_number, questions=new_questions)


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
            return _neutralize_mermaid_labels(quiz)
        if attempt < max_mermaid_retries:
            retried = llm.generate_quiz(req)
            quiz = Quiz(version="1", pr_number=pr_number, questions=retried.questions)

    # Last resort: drop mermaid questions
    kept: list[Question] = [q for q in quiz.questions if not isinstance(q, MermaidQuestion)]
    return Quiz(version="1", pr_number=pr_number, questions=kept)
