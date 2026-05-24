from cognit.engine.generate import generate_quiz
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
