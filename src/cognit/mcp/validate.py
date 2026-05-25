"""Validate an agent-submitted quiz draft and prepare it for rendering.

Pure port of the SDK submit-validation hook (llm_claude_agent.py:_submit_validation_hook):
Pydantic shape, then per-mermaid checks (exactly 4 options, answer in keys, each diagram
parses, the four are uniform AND distinct), then a missing-`explanation` check. On success
wraps into a Quiz(pr_number=...) and runs the answer-position shuffle
(engine.generate._neutralize_mermaid_labels) — load-bearing anti-leak, see that function.

Returns (Quiz, []) on success or (None, [reasons]) — the reasons are handed back to the
agent so it self-corrects, exactly as the SDK hook's deny reason did.
"""

from __future__ import annotations

from pydantic import ValidationError

from cognit.engine.generate import _neutralize_mermaid_labels
from cognit.engine.mermaid import distinctness_failure, is_valid_mermaid, uniformity_failures
from cognit.engine.models import (
    MCQQuestion,
    MermaidQuestion,
    Quiz,
    QuizDraft,
    TrueFalseQuestion,
)


def validate_and_prepare(  # noqa: C901
    draft: dict[str, object], *, pr_number: int
) -> tuple[Quiz | None, list[str]]:
    """Validate a raw agent-submitted draft dict and, on success, return a shuffled Quiz.

    This is synchronous and may block for seconds per diagram when ``mmdc``/``docker`` are
    installed (it shells out to them via ``is_valid_mermaid``); call it from a worker thread,
    not directly on an event loop.
    """
    try:
        parsed = QuizDraft.model_validate(draft)
    except ValidationError as e:
        return None, [f"the submitted quiz is malformed: {e.errors()}"]

    failures: list[str] = []
    for q in parsed.questions:
        if (
            isinstance(q, (MCQQuestion, TrueFalseQuestion, MermaidQuestion))
            and not q.explanation.strip()
        ):
            failures.append(
                f"question {q.id!r}: missing a one-sentence `explanation` "
                "(shown to the reader after they answer)"
            )
        if not isinstance(q, MermaidQuestion):
            continue
        if len(q.options) != 4:
            failures.append(
                f"question {q.id!r}: must have exactly 4 options, has {len(q.options)}"
            )
            continue
        if q.answer not in q.options:  # defense-in-depth: Pydantic's model_validator already enforces this, so this is normally unreachable
            failures.append(f"question {q.id!r}: answer {q.answer!r} is not one of the option keys")
        for label, src in q.options.items():
            if not is_valid_mermaid(src, strict=False):
                failures.append(f"question {q.id!r} option {label}: invalid mermaid syntax")
        failures.extend(f"question {q.id!r}: {m}" for m in uniformity_failures(q.options))
        failures.extend(f"question {q.id!r}: {m}" for m in distinctness_failure(q.options))

    if failures:
        return None, failures

    quiz = Quiz(version="1", pr_number=pr_number, questions=parsed.questions)
    return _neutralize_mermaid_labels(quiz), []
