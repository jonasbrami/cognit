import pytest
from pydantic import ValidationError
from cognit.engine.models import (
    Anchor,
    Quiz,
    MCQQuestion,
    MermaidQuestion,
    OpenQuestion,
    TrueFalseQuestion,
    Answers,
    AnswerEntry,
    Results,
    QuestionResult,
)


def test_mcq_question_round_trip():
    q = MCQQuestion(id="q1", prompt="Why?", options=["A", "B", "C"], answer="B")
    data = q.model_dump()
    assert MCQQuestion.model_validate(data) == q


def test_mcq_answer_must_be_one_of_options():
    with pytest.raises(ValidationError):
        MCQQuestion(id="q1", prompt="Why?", options=["A", "B"], answer="Z")


def test_mermaid_question():
    q = MermaidQuestion(
        id="q2",
        prompt="Which diagram?",
        options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"},
        answer="A",
    )
    assert q.answer == "A"


def test_open_question():
    q = OpenQuestion(id="q3", prompt="Explain.", rubric="Mentions X, Y.")
    assert q.rubric.startswith("Mentions")


def test_tf_question():
    q = TrueFalseQuestion(id="q4", prompt="Is it?", answer=True)
    assert q.answer is True


def test_quiz_discriminated_union():
    quiz = Quiz(
        version="1",
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A"),
            OpenQuestion(id="q3", prompt="?", rubric="r"),
        ],
    )
    raw = quiz.model_dump_json()
    parsed = Quiz.model_validate_json(raw)
    assert parsed == quiz
    assert isinstance(parsed.questions[0], MCQQuestion)
    assert isinstance(parsed.questions[1], OpenQuestion)


def test_answers_results():
    a = Answers(pr_number=42, entries=[AnswerEntry(question_id="q1", value="A")])
    r = Results(
        pr_number=42,
        total_score=80,
        per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")],
    )
    assert a.entries[0].value == "A"
    assert r.total_score == 80


def test_mermaid_answer_must_be_one_of_options():
    with pytest.raises(ValidationError):
        MermaidQuestion(
            id="q2",
            prompt="?",
            options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"},
            answer="Z",
        )


def test_score_must_be_in_0_100():
    with pytest.raises(ValidationError):
        QuestionResult(question_id="q1", correct=True, score=101, feedback="")
    with pytest.raises(ValidationError):
        QuestionResult(question_id="q1", correct=True, score=-1, feedback="")
    with pytest.raises(ValidationError):
        Results(pr_number=1, total_score=150, per_question=[])


def test_objective_questions_carry_optional_explanation():
    mcq = MCQQuestion(
        id="q1",
        prompt="?",
        options=["a", "b"],
        answer="a",
        explanation="b is wrong because it returns the cached value, not a fresh read.",
    )
    assert mcq.explanation.startswith("b is wrong")
    # default is empty (backward compatible with existing fixtures)
    assert MCQQuestion(id="q2", prompt="?", options=["a", "b"], answer="a").explanation == ""
    assert TrueFalseQuestion(id="q3", prompt="?", answer=True).explanation == ""
    assert (
        MermaidQuestion(
            id="q4", prompt="?", options={"A": "flowchart LR\nA-->B"}, answer="A"
        ).explanation
        == ""
    )


def test_anchor_round_trip():
    a = Anchor(path="src/cognit/mcp/state.py", start_line=40, end_line=46)
    assert Anchor.model_validate(a.model_dump()) == a


def test_anchor_single_line_ok():
    a = Anchor(path="x.py", start_line=12, end_line=12)
    assert a.start_line == a.end_line == 12


def test_anchor_rejects_reversed_range():
    with pytest.raises(ValidationError):
        Anchor(path="x.py", start_line=10, end_line=5)


def test_anchor_rejects_nonpositive_lines():
    with pytest.raises(ValidationError):
        Anchor(path="x.py", start_line=0, end_line=4)


def test_questions_carry_optional_anchor():
    anchor = Anchor(path="src/a.py", start_line=3, end_line=9)
    mcq = MCQQuestion(id="q1", prompt="?", options=["a", "b"], answer="a", anchor=anchor)
    assert mcq.anchor == anchor
    # every type accepts an anchor
    assert (
        MermaidQuestion(
            id="q2", prompt="?", options={"A": "flowchart LR\nA-->B"}, answer="A", anchor=anchor
        ).anchor
        == anchor
    )
    assert OpenQuestion(id="q3", prompt="?", rubric="r", anchor=anchor).anchor == anchor
    assert TrueFalseQuestion(id="q4", prompt="?", answer=True, anchor=anchor).anchor == anchor


def test_anchor_defaults_to_none_backward_compatible():
    # questions without an anchor (old cached quizzes) still validate, anchor is None
    assert MCQQuestion(id="q1", prompt="?", options=["a", "b"], answer="a").anchor is None
    assert OpenQuestion(id="q3", prompt="?", rubric="r").anchor is None
    # and a quiz dict produced before the field existed round-trips
    legacy = {
        "version": "1",
        "pr_number": 7,
        "questions": [
            {"type": "mcq", "id": "q1", "prompt": "?", "options": ["a", "b"], "answer": "a"}
        ],
    }
    quiz = Quiz.model_validate(legacy)
    assert quiz.questions[0].anchor is None
