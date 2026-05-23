from cognit.comment.render import render_quiz
from cognit.engine.models import Quiz, MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion


def _sample_quiz() -> Quiz:
    return Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="Why X?", options=["A", "B", "C"], answer="B"),
            MermaidQuestion(
                id="q2",
                prompt="Pick the flow:",
                options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"},
                answer="A",
            ),
            OpenQuestion(id="q3", prompt="Explain.", rubric="mentions safety"),
            TrueFalseQuestion(id="q4", prompt="Is it?", answer=True),
        ],
    )


def test_render_includes_marker():
    md = render_quiz(_sample_quiz())
    assert "<!-- cognit:quiz v1 -->" in md


def test_render_mermaid_uses_code_fence():
    md = render_quiz(_sample_quiz())
    assert "```mermaid" in md
    assert "flowchart LR" in md


def test_render_embeds_json_state():
    md = render_quiz(_sample_quiz())
    assert "```json" in md
    assert '"pr_number": 42' in md
