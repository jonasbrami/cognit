"""Single-stage quiz generation.

One agentic call (`llm.draft_quiz`) produces the complete quiz with mermaid
fully rendered; a submit-validation hook inside the adapter guarantees every
diagram parses and the four options are visually uniform. This module just
builds the request, wraps the draft into a `Quiz`, and shuffles mermaid option
labels (defense-in-depth against the model's correct-answer-position bias).
"""

import random

from cognit.engine.llm import GenerateRequest, LLMClient
from cognit.engine.models import MermaidQuestion, Question, Quiz


def _neutralize_mermaid_labels(quiz: Quiz, rng: random.Random | None = None) -> Quiz:
    """Rewrite each MermaidQuestion's option keys to neutral A/B/C/D and shuffle order.

    LOAD-BEARING — not just defense-in-depth. The submit schema enforces A/B/C/D
    keys, but Claude tends to put the correct answer under "A" most of the time. This
    shuffle is what breaks that bias. Removing it would visibly leak the answer.
    """
    if rng is None:
        rng = random.Random()
    new_questions: list[Question] = []
    for q in quiz.questions:
        if not isinstance(q, MermaidQuestion):
            new_questions.append(q)
            continue
        items = list(q.options.items())
        rng.shuffle(items)
        letters = ["A", "B", "C", "D", "E", "F"][: len(items)]
        new_options = {letters[i]: items[i][1] for i in range(len(items))}
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
    pr_title: str,
    pr_body: str,
    pr_number: int,
    pr_url: str,
    branch: str,
    llm: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Quiz:
    req = GenerateRequest(
        pr_title=pr_title,
        pr_body=pr_body,
        pr_number=pr_number,
        pr_url=pr_url,
        branch=branch,
        model=model,
    )
    draft = llm.draft_quiz(req)
    quiz = Quiz(version="1", pr_number=pr_number, questions=draft.questions)
    return _neutralize_mermaid_labels(quiz)
