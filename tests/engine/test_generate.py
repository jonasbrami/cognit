from pathlib import Path

import pytest

from quizz.engine.generate import generate_quiz
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import MCQQuestion, MermaidQuestion, OpenQuestion, Quiz

FIX = Path(__file__).parent.parent / "fixtures"


def test_generate_returns_quiz_via_llm() -> None:
    """Non-mermaid questions pass through unchanged; mermaid options get relabeled to A/B/C/D."""
    diff = (FIX / "diffs" / "small_refactor.patch").read_text()
    canned = Quiz(
        pr_number=1,
        questions=[
            MCQQuestion(id="q1", prompt="why lock?", options=["safety", "speed"], answer="safety"),
            MermaidQuestion(
                id="q2",
                prompt="which flow?",
                options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"},
                answer="A",
            ),
            OpenQuestion(id="q3", prompt="rationale?", rubric="thread safety"),
        ],
    )
    out = generate_quiz(
        diff=diff,
        pr_title="add lock",
        pr_body="",
        files={"cache.py": "..."},
        pr_number=1,
        llm=FakeLLM(canned_quiz=canned),
    )
    # MCQ and Open pass through unchanged.
    assert out.questions[0] == canned.questions[0]
    assert out.questions[2] == canned.questions[2]
    # Mermaid keeps the same id/prompt; options use A/B...; answer still points to "A-->B".
    mq = out.questions[1]
    assert isinstance(mq, MermaidQuestion)
    assert mq.id == "q2"
    assert set(mq.options.keys()) <= {"A", "B", "C", "D", "E", "F"}
    assert mq.options[mq.answer] == "flowchart LR\nA-->B"


def test_mermaid_labels_neutralized_when_llm_uses_semantic_keys() -> None:
    """LLMs sometimes emit 'correct'/'wrong_1' — those must be relabeled to A/B/C."""
    canned = Quiz(
        pr_number=1,
        questions=[
            MermaidQuestion(
                id="m1",
                prompt="?",
                options={
                    "correct": "flowchart LR\nA-->B",
                    "wrong_1": "flowchart LR\nB-->A",
                    "wrong_2": "flowchart LR\nC-->D",
                },
                answer="correct",
            ),
        ],
    )
    out = generate_quiz(
        diff="x",
        pr_title="t",
        pr_body="",
        files={},
        pr_number=1,
        llm=FakeLLM(canned_quiz=canned),
    )
    [q] = out.questions
    assert isinstance(q, MermaidQuestion)
    assert set(q.options.keys()) == {"A", "B", "C"}
    # The answer should still point to the formerly-"correct" source content.
    assert q.options[q.answer] == "flowchart LR\nA-->B"


def test_generate_drops_invalid_mermaid(monkeypatch: pytest.MonkeyPatch) -> None:
    """If mmdc rejects every mermaid candidate after retries, drop the mermaid Q."""
    monkeypatch.setattr("quizz.engine.generate._validate_mermaid", lambda src: False)
    canned = Quiz(
        pr_number=1,
        questions=[
            MermaidQuestion(
                id="q1",
                prompt="?",
                options={"A": "bad", "B": "bad"},
                answer="A",
            ),
            MCQQuestion(id="q2", prompt="?", options=["x", "y"], answer="x"),
        ],
    )
    out = generate_quiz(
        diff="x",
        pr_title="t",
        pr_body="",
        files={},
        pr_number=1,
        llm=FakeLLM(canned_quiz=canned),
        max_mermaid_retries=0,
    )
    assert not any(q.type == "mermaid" for q in out.questions)
