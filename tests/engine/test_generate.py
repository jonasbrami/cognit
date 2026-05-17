from pathlib import Path
from quizz.engine.generate import generate_quiz
from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import Quiz, MCQQuestion, MermaidQuestion, OpenQuestion

FIX = Path(__file__).parent.parent / "fixtures"


def test_generate_returns_quiz_via_llm():
    diff = (FIX / "diffs" / "small_refactor.patch").read_text()
    canned = Quiz(
        pr_number=1,
        questions=[
            MCQQuestion(id="q1", prompt="why lock?", options=["safety","speed"], answer="safety"),
            MermaidQuestion(
                id="q2", prompt="which flow?",
                options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"}, answer="A",
            ),
            OpenQuestion(id="q3", prompt="rationale?", rubric="thread safety"),
        ],
    )
    out = generate_quiz(
        diff=diff, pr_title="add lock", pr_body="", files={"cache.py": "..."},
        pr_number=1, llm=FakeLLM(canned_quiz=canned),
    )
    assert out == canned


def test_generate_drops_invalid_mermaid(monkeypatch):
    """If mmdc rejects every mermaid candidate after retries, drop the mermaid Q."""
    monkeypatch.setattr("quizz.engine.generate._validate_mermaid", lambda src: False)
    canned = Quiz(
        pr_number=1,
        questions=[
            MermaidQuestion(
                id="q1", prompt="?",
                options={"A": "bad", "B": "bad"}, answer="A",
            ),
            MCQQuestion(id="q2", prompt="?", options=["x","y"], answer="x"),
        ],
    )
    out = generate_quiz(
        diff="x", pr_title="t", pr_body="", files={},
        pr_number=1, llm=FakeLLM(canned_quiz=canned), max_mermaid_retries=0,
    )
    assert not any(q.type == "mermaid" for q in out.questions)
