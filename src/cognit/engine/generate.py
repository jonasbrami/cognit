"""Single-stage quiz generation.

One agentic call (`llm.draft_quiz`) produces the complete quiz with mermaid
fully rendered; a submit-validation hook inside the adapter guarantees every
diagram parses and the four options are visually uniform. This module just
builds the request, wraps the draft into a `Quiz`, and shuffles mermaid option
labels (defense-in-depth against the model's correct-answer-position bias).

The one expensive call here — the agentic draft — is also the one that can fail
transiently (rate limits, provider overload, a dropped connection mid-stream).
`_draft_with_retry` retries those, and *only* those, with jittered exponential
backoff; deterministic failures (a malformed submission, an exhausted turn
budget) surface immediately.
"""

import logging
import random
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from cognit.engine.llm import GenerateRequest, LLMClient
from cognit.engine.models import MermaidQuestion, Question, Quiz, QuizDraft

logger = logging.getLogger("cognit.engine.generate")

# Retry policy for the agentic draft. Three total attempts is enough to ride out
# a brief rate-limit window without turning a hard outage into a multi-minute hang.
_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 2.0
_MAX_DELAY_S = 30.0

# Substrings that mark a *transient* failure. The claude-agent adapter collapses
# every SDK failure into a `RuntimeError` carrying the upstream text, so the
# message — not the exception type — is what we classify on.
_RETRYABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "429",
    "overloaded",
    "529",
    "503",
    "service unavailable",
    "timed out",
    "timeout",
    "connection reset",
    "connection error",
)

# Failures that can look transient but never are — retrying only burns the same
# tokens to the same dead end, so we surface them on the first attempt.
_FATAL_MARKERS = (
    "binary not found",  # claude is not installed / not on PATH
    "maximum number of turns",  # the agent exhausted its turn budget
)


def _is_retryable(exc: Exception) -> bool:
    """True only for transient upstream failures worth another attempt.

    A `ValidationError` means the model submitted a malformed quiz — deterministic,
    so retrying would just reproduce it. Everything else is judged by its message:
    a fatal marker (missing binary, turn-budget exhaustion) is never retried; a
    transient marker (rate limit, overload, dropped connection) is.
    """
    if isinstance(exc, ValidationError):
        return False
    text = str(exc).lower()
    if any(marker in text for marker in _FATAL_MARKERS):
        return False
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _backoff_delay(attempt: int, rng: random.Random) -> float:
    """Exponential backoff with full jitter: a random wait in
    `[0, min(_MAX_DELAY_S, _BASE_DELAY_S * 2**attempt)]`.

    The jitter is load-bearing, not cosmetic. If many clients retried on the same
    fixed schedule they would re-collide on every wave — the "thundering herd" that
    keeps an overloaded service overloaded. Spreading each client's wait uniformly
    across the whole window decorrelates them. `attempt` is 0-based, so the cap
    grows 2s → 4s → 8s … until it saturates at `_MAX_DELAY_S`.
    """
    capped = min(_MAX_DELAY_S, _BASE_DELAY_S * (2**attempt))
    return rng.uniform(0.0, capped)


def _draft_with_retry(
    llm: LLMClient,
    req: GenerateRequest,
    *,
    rng: random.Random,
    sleep: Callable[[float], None] = time.sleep,
) -> QuizDraft:
    """Call `llm.draft_quiz`, retrying transient failures with jittered backoff.

    Up to `_MAX_ATTEMPTS` total attempts. A non-retryable error — or the final
    attempt — re-raises the original exception unchanged, so the caller's error
    handling (and the activity feed) sees the real cause, not a wrapper. The retry
    notice is forwarded to the LLM's activity sink (if any) so a streamed run shows
    the wait rather than going silent.
    """
    sink: Callable[[dict[str, Any]], None] | None = getattr(llm, "on_event", None)
    last_exc: Exception
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return llm.draft_quiz(req)
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= _MAX_ATTEMPTS or not _is_retryable(exc):
                raise
            delay = _backoff_delay(attempt, rng)
            logger.debug(
                "draft_quiz attempt %d/%d failed (%s); retrying in %.1fs",
                attempt + 1,
                _MAX_ATTEMPTS,
                exc,
                delay,
            )
            if callable(sink):
                sink(
                    {
                        "kind": "text",
                        "text": (
                            f"transient error — retrying "
                            f"({attempt + 2}/{_MAX_ATTEMPTS}) in {delay:.0f}s…"
                        ),
                        "tool": "submit_quiz",
                    }
                )
            sleep(delay)
    raise last_exc  # unreachable: the final attempt always returns or raises above


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
    draft = _draft_with_retry(llm, req, rng=random.Random())
    quiz = Quiz(version="1", pr_number=pr_number, questions=draft.questions)
    return _neutralize_mermaid_labels(quiz)
