from pathlib import Path

import pytest

from cognit.engine.generate import generate_quiz
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import (
    MCQQuestion,
    MermaidPlaceholder,
    MermaidQuestion,
    MermaidSet,
    MermaidSpec,
    OpenQuestion,
    QuizOutline,
)

FIX = Path(__file__).parent.parent / "fixtures"


def _placeholder() -> MermaidPlaceholder:
    return MermaidPlaceholder(
        id="q2",
        prompt="which flow?",
        spec=MermaidSpec(
            diagram_type="flowchart",
            correct_description="A -> B",
            misconceptions=["reversed", "missing edge", "extra fork"],
            style_notes="2 nodes, LR",
        ),
    )


def test_generate_renders_mermaid_via_subagent_and_passes_others_through() -> None:
    """Outline stage emits MCQ/Open/MermaidPlaceholder; subagent renders the placeholder
    into a MermaidQuestion; final Quiz contains the rendered question + the pass-throughs."""
    outline = QuizOutline(
        questions=[
            MCQQuestion(id="q1", prompt="why lock?", options=["safety", "speed"], answer="safety"),
            _placeholder(),
            OpenQuestion(id="q3", prompt="rationale?", rubric="thread safety"),
        ],
    )
    mset = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nC-->B",
        },
        correct="A",
    )
    diff = (FIX / "diffs" / "small_refactor.patch").read_text()
    out = generate_quiz(
        diff=diff,
        pr_title="add lock",
        pr_body="",
        files={"cache.py": "..."},
        pr_number=1,
        llm=FakeLLM(canned_outline=outline, canned_mermaid=mset),
    )
    assert out.pr_number == 1
    # MCQ and Open pass through unchanged.
    assert out.questions[0] == outline.questions[0]
    assert out.questions[2] == outline.questions[2]
    # Placeholder is replaced by a rendered MermaidQuestion.
    mq = out.questions[1]
    assert isinstance(mq, MermaidQuestion)
    assert mq.id == "q2"
    assert mq.prompt == "which flow?"
    assert set(mq.options.keys()) == {"A", "B", "C", "D"}
    # The answer key may have been shuffled by _neutralize_mermaid_labels, but it must
    # still point at the originally-correct source.
    assert mq.options[mq.answer] == "flowchart LR\nA-->B"


def test_mermaid_labels_are_shuffled_after_subagent() -> None:
    """The artisan's correct=A output should not always remain under key A after the
    engine's defense-in-depth shuffle pass. We assert the *content* mapping is stable
    instead of the key. This test exercises the shuffle path without flaking — running
    enough iterations to make a non-shuffle bug detectable."""
    placeholder = _placeholder()
    mset = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nC-->B",
        },
        correct="A",
    )
    seen_answer_keys: set[str] = set()
    for _ in range(20):
        out = generate_quiz(
            diff="x",
            pr_title="t",
            pr_body="",
            files={},
            pr_number=1,
            llm=FakeLLM(
                canned_outline=QuizOutline(questions=[placeholder]),
                canned_mermaid=mset,
            ),
        )
        mq = out.questions[0]
        assert isinstance(mq, MermaidQuestion)
        # The correct source content never changes.
        assert mq.options[mq.answer] == "flowchart LR\nA-->B"
        seen_answer_keys.add(mq.answer)
    # Across 20 shuffles, we should land on more than one key for the correct answer.
    assert len(seen_answer_keys) > 1, "expected shuffle to vary the answer key over runs"


def test_generate_drops_invalid_mermaid(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the artisan keeps producing invalid mermaid after retries, drop the question."""
    monkeypatch.setattr("cognit.engine.generate._validate_mermaid", lambda src: False)
    outline = QuizOutline(
        questions=[
            _placeholder(),
            MCQQuestion(id="q2", prompt="?", options=["x", "y"], answer="x"),
        ],
    )
    out = generate_quiz(
        diff="x",
        pr_title="t",
        pr_body="",
        files={},
        pr_number=1,
        llm=FakeLLM(
            canned_outline=outline,
            canned_mermaid=MermaidSet(
                options={"A": "bad", "B": "bad", "C": "bad", "D": "bad"},
                correct="A",
            ),
        ),
        max_mermaid_retries=0,
    )
    assert not any(q.type == "mermaid" for q in out.questions)
    assert any(q.type == "mcq" for q in out.questions)


def test_generate_retries_artisan_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """First artisan call returns invalid mermaid; retry returns valid. Engine must
    end up with the rendered question, not drop it."""
    valid_set = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nC-->B",
        },
        correct="A",
    )
    invalid_set = MermaidSet(
        options={"A": "bad", "B": "bad", "C": "bad", "D": "bad"},
        correct="A",
    )

    calls = {"n": 0}

    def flaky(spec: MermaidSpec) -> MermaidSet:
        calls["n"] += 1
        return invalid_set if calls["n"] == 1 else valid_set

    # Force the real validator to only accept "flowchart LR" sources, so the invalid_set
    # fails and the valid_set passes — without depending on mmdc being installed.
    monkeypatch.setattr(
        "cognit.engine.generate._validate_mermaid",
        lambda src: src.startswith("flowchart"),
    )
    outline = QuizOutline(questions=[_placeholder()])
    out = generate_quiz(
        diff="x",
        pr_title="t",
        pr_body="",
        files={},
        pr_number=1,
        llm=FakeLLM(canned_outline=outline, canned_mermaid=flaky),
        max_mermaid_retries=2,
    )
    assert calls["n"] == 2, "artisan should have been called twice (one bad, one good)"
    assert len(out.questions) == 1
    assert isinstance(out.questions[0], MermaidQuestion)


def test_generate_survives_validation_error_from_artisan(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the artisan raises a pydantic ValidationError (malformed tool input from the
    LLM), the engine must catch it, retry once, and continue — not crash the whole quiz."""
    from pydantic import ValidationError

    valid_set = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nC-->B",
        },
        correct="A",
    )

    calls = {"n": 0}

    def boom_then_ok(spec: MermaidSpec) -> MermaidSet:
        calls["n"] += 1
        if calls["n"] == 1:
            # Trigger a real ValidationError via the model so we exercise the actual
            # except clause in _render_mermaid_with_retry.
            MermaidSet(options={"A": "x"}, correct="A")  # missing B/C/D — raises
        return valid_set

    # Side-step mmdc: any "flowchart …" source is accepted.
    monkeypatch.setattr(
        "cognit.engine.generate._validate_mermaid",
        lambda src: src.startswith("flowchart"),
    )
    outline = QuizOutline(
        questions=[
            _placeholder(),
            MCQQuestion(id="q2", prompt="ok", options=["x", "y"], answer="x"),
        ],
    )
    # Verify the ValidationError shape we'll catch is actually a pydantic one.
    with pytest.raises(ValidationError):
        MermaidSet(options={"A": "x"}, correct="A")
    out = generate_quiz(
        diff="x",
        pr_title="t",
        pr_body="",
        files={},
        pr_number=1,
        llm=FakeLLM(canned_outline=outline, canned_mermaid=boom_then_ok),
        max_mermaid_retries=2,
    )
    assert calls["n"] == 2
    assert len(out.questions) == 2  # mermaid recovered + MCQ pass-through
    assert any(isinstance(q, MermaidQuestion) for q in out.questions)
