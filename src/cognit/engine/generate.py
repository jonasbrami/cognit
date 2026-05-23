"""Two-stage quiz generation orchestrator.

Stage 1 (outline): a single LLM call decides the questions and emits structured
specs for any mermaid questions instead of raw diagram syntax.

Stage 2 (mermaid artisans): for each mermaid placeholder in the outline, dispatch
a focused subagent to render 4 uniform diagrams. Subagent calls run in parallel
(thread pool — Anthropic SDK is sync I/O) and retry per-question on validation
failure. As a last resort the mermaid question is dropped.
"""

import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import ValidationError

from cognit.engine.llm import GenerateRequest, LLMClient
from cognit.engine.mermaid import is_valid_mermaid
from cognit.engine.models import (
    MermaidPlaceholder,
    MermaidQuestion,
    MermaidSet,
    Question,
    Quiz,
)


def _validate_mermaid(source: str) -> bool:
    return is_valid_mermaid(source, strict=False)


def _validate_set(mset: MermaidSet) -> bool:
    return all(_validate_mermaid(src) for src in mset.options.values())


def _render_mermaid_with_retry(
    placeholder: MermaidPlaceholder,
    req: GenerateRequest,
    llm: LLMClient,
    max_retries: int,
) -> MermaidQuestion | None:
    """Call the mermaid artisan subagent until it produces valid diagrams or we give up.

    Returns the rendered MermaidQuestion on success, or None to signal "drop this question."
    Catches schema-shape failures (ValidationError) and tool-extraction failures (RuntimeError)
    in addition to mermaid-syntax validity — one bad artisan call must not abort the whole quiz.
    """
    for _ in range(max_retries + 1):
        try:
            mset = llm.generate_mermaid_set(placeholder.spec, req)
        except (ValidationError, RuntimeError):
            continue
        if _validate_set(mset):
            return MermaidQuestion(
                id=placeholder.id,
                prompt=placeholder.prompt,
                options=mset.options,
                answer=mset.correct,
            )
    return None


def _neutralize_mermaid_labels(quiz: Quiz, rng: random.Random | None = None) -> Quiz:
    """Rewrite each MermaidQuestion's option keys to neutral A/B/C/D and shuffle order.

    LOAD-BEARING — not just defense-in-depth. The artisan's schema enforces A/B/C/D
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
    max_mermaid_retries: int = 2,
    model: str = "claude-sonnet-4-6",
    max_mermaid_workers: int = 1,
) -> Quiz:
    # `max_mermaid_workers=1` (serial) is safe for hobby-tier accounts; bursting
    # 4+ concurrent Sonnet calls in the same second is a realistic 429 trigger on tier-1
    # rate limits. Bump this for accounts that have headroom.
    # NOTE: values >1 also race the adapter's shared `_current_tool` activity label
    # (read by the streaming sink), which would mislabel the live feed — localize that
    # per-call before parallelizing.
    req = GenerateRequest(
        pr_title=pr_title,
        pr_body=pr_body,
        pr_number=pr_number,
        pr_url=pr_url,
        branch=branch,
        model=model,
    )

    outline = llm.generate_quiz_outline(req)

    # Identify mermaid placeholders and dispatch artisan subagents in parallel.
    placeholders: list[tuple[int, MermaidPlaceholder]] = [
        (i, q) for i, q in enumerate(outline.questions) if isinstance(q, MermaidPlaceholder)
    ]
    rendered: dict[int, MermaidQuestion | None] = {}
    if placeholders:
        workers = min(max_mermaid_workers, len(placeholders))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_render_mermaid_with_retry, q, req, llm, max_mermaid_retries): i
                for i, q in placeholders
            }
            for fut in as_completed(futures):
                rendered[futures[fut]] = fut.result()

    final_questions: list[Question] = []
    for i, q in enumerate(outline.questions):
        if isinstance(q, MermaidPlaceholder):
            mq = rendered.get(i)
            if mq is not None:
                final_questions.append(mq)
            # otherwise: drop this question (last-resort fallback)
        else:
            final_questions.append(q)

    quiz = Quiz(version="1", pr_number=pr_number, questions=final_questions)
    return _neutralize_mermaid_labels(quiz)
