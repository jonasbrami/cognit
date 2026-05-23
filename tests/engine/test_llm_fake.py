from cognit.engine.llm import GenerateRequest, LLMClient
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, MermaidSet, QuizOutline


def test_fake_returns_canned_outline() -> None:
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    llm: LLMClient = FakeLLM(canned_outline=canned)
    out = llm.generate_quiz_outline(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))
    assert out == canned


def test_fake_returns_canned_mermaid_set() -> None:
    mset = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nC-->A",
        },
        correct="A",
    )
    llm = FakeLLM(canned_mermaid=mset)
    from cognit.engine.models import MermaidSpec

    out = llm.generate_mermaid_set(
        MermaidSpec(
            diagram_type="flowchart",
            correct_description="x",
            misconceptions=["a", "b", "c"],
            style_notes="n",
        ),
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}),
    )
    assert out == mset


def test_fake_grades_open_question() -> None:
    llm = FakeLLM(canned_open_score=75, canned_open_feedback="ok")
    score, fb = llm.grade_open(
        question_prompt="why?",
        rubric="r",
        answer="because",
    )
    assert score == 75
    assert fb == "ok"
