import random

import pytest
from pydantic import ValidationError

from cognit.engine.generate import (
    _MAX_ATTEMPTS,
    _MAX_DELAY_S,
    _backoff_delay,
    _draft_with_retry,
    _is_retryable,
    generate_quiz,
)
from cognit.engine.llm import GenerateRequest
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import (
    MCQQuestion,
    MermaidQuestion,
    OpenQuestion,
    QuizDraft,
)


def _draft_with_mermaid() -> QuizDraft:
    return QuizDraft(
        questions=[
            MCQQuestion(id="q1", prompt="why lock?", options=["safety", "speed"], answer="safety"),
            MermaidQuestion(
                id="q2",
                prompt="which flow?",
                options={
                    "A": "flowchart LR\nA-->B",
                    "B": "flowchart LR\nB-->A",
                    "C": "flowchart LR\nA-->C",
                    "D": "flowchart LR\nC-->B",
                },
                answer="A",
            ),
            OpenQuestion(id="q3", prompt="rationale?", rubric="thread safety"),
        ]
    )


def test_generate_wraps_draft_into_quiz_and_passes_through() -> None:
    draft = _draft_with_mermaid()
    out = generate_quiz(
        pr_title="add lock",
        pr_body="",
        pr_number=1,
        pr_url="https://github.com/o/r/pull/1",
        branch="br",
        llm=FakeLLM(canned_draft=draft),
    )
    assert out.pr_number == 1
    # MCQ and Open pass through unchanged.
    assert out.questions[0] == draft.questions[0]
    assert out.questions[2] == draft.questions[2]
    mq = out.questions[1]
    assert isinstance(mq, MermaidQuestion)
    assert set(mq.options.keys()) == {"A", "B", "C", "D"}
    # Label may be shuffled, but the correct source content is preserved.
    assert mq.options[mq.answer] == "flowchart LR\nA-->B"


def test_mermaid_labels_are_shuffled() -> None:
    seen_answer_keys: set[str] = set()
    for _ in range(20):
        out = generate_quiz(
            pr_title="t",
            pr_body="",
            pr_number=1,
            pr_url="https://github.com/o/r/pull/1",
            branch="br",
            llm=FakeLLM(canned_draft=_draft_with_mermaid()),
        )
        mq = out.questions[1]
        assert isinstance(mq, MermaidQuestion)
        assert mq.options[mq.answer] == "flowchart LR\nA-->B"
        seen_answer_keys.add(mq.answer)
    assert len(seen_answer_keys) > 1, "expected shuffle to vary the answer key over runs"


# --- retry-with-backoff ---------------------------------------------------


class _FlakyLLM:
    """draft_quiz raises the queued exceptions in order, then returns `draft`.

    Records every retry-notice event pushed to `on_event` so a test can assert the
    streamed feed reflects the waits.
    """

    def __init__(self, raises: list[Exception], draft: QuizDraft):
        self._raises = list(raises)
        self._draft = draft
        self.calls = 0
        self.events: list[dict] = []
        self.on_event = self.events.append

    def draft_quiz(self, req: GenerateRequest) -> QuizDraft:
        self.calls += 1
        if self._raises:
            raise self._raises.pop(0)
        return self._draft

    def grade_open(self, prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        return 100, ""


def _req() -> GenerateRequest:
    return GenerateRequest(
        pr_title="t", pr_body="", pr_number=1, pr_url="https://github.com/o/r/pull/1", branch="br"
    )


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("claude agent SDK call failed: 429 rate limit exceeded"), True),
        (RuntimeError("Error: Overloaded (529)"), True),
        (RuntimeError("connection reset by peer"), True),
        (RuntimeError("request timed out"), True),
        # deterministic / fatal — never retried
        (RuntimeError("claude binary not found; install Claude Code"), False),
        (RuntimeError("claude agent SDK error: Reached maximum number of turns"), False),
        (ValidationError.from_exception_data("QuizDraft", []), False),
        (RuntimeError("agent did not call submit_quiz"), False),
    ],
)
def test_is_retryable_classifies_by_cause(exc: Exception, expected: bool) -> None:
    assert _is_retryable(exc) is expected


def test_backoff_delay_grows_but_stays_within_jittered_cap() -> None:
    rng = random.Random(0)
    for attempt in range(6):
        cap = min(_MAX_DELAY_S, 2.0 * (2**attempt))
        for _ in range(50):
            d = _backoff_delay(attempt, rng)
            assert 0.0 <= d <= cap


def test_retries_transient_then_succeeds_and_notifies_feed() -> None:
    draft = _draft_with_mermaid()
    llm = _FlakyLLM([RuntimeError("429 rate limit")], draft)
    slept: list[float] = []
    out = _draft_with_retry(llm, _req(), rng=random.Random(0), sleep=slept.append)
    assert out is draft
    assert llm.calls == 2  # one failure + one success
    assert len(slept) == 1
    assert any("retrying" in e["text"] for e in llm.events)


def test_non_retryable_raises_immediately_without_sleeping() -> None:
    llm = _FlakyLLM([RuntimeError("claude binary not found")], _draft_with_mermaid())
    slept: list[float] = []
    with pytest.raises(RuntimeError, match="binary not found"):
        _draft_with_retry(llm, _req(), rng=random.Random(0), sleep=slept.append)
    assert llm.calls == 1
    assert slept == []


def test_exhausts_attempts_then_raises_last_error() -> None:
    transient = [RuntimeError(f"429 rate limit #{i}") for i in range(_MAX_ATTEMPTS)]
    llm = _FlakyLLM(transient, _draft_with_mermaid())
    slept: list[float] = []
    with pytest.raises(RuntimeError, match=f"#{_MAX_ATTEMPTS - 1}"):
        _draft_with_retry(llm, _req(), rng=random.Random(0), sleep=slept.append)
    assert llm.calls == _MAX_ATTEMPTS
    assert len(slept) == _MAX_ATTEMPTS - 1  # no sleep after the final failure
