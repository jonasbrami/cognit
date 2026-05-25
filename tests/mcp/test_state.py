import json
from pathlib import Path

import pytest

from cognit.engine.models import MCQQuestion, QuestionResult, Quiz, Results
from cognit.mcp.state import QuizState


def _quiz() -> Quiz:
    return Quiz(
        pr_number=7,
        questions=[MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A")],
    )


def test_set_quiz_persists_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "snap.json"
    s = QuizState(pr_number=7, snapshot_path=snap)
    s.set_quiz(_quiz())
    assert s.quiz is not None and s.quiz.questions[0].id == "q1"
    data = json.loads(snap.read_text())
    assert data["quiz"]["questions"][0]["id"] == "q1"


def test_record_answer(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    s.set_quiz(_quiz())
    s.record_answer("q1", "A")
    assert s.answers == {"q1": "A"}


def test_loads_existing_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "s.json"
    QuizState(pr_number=7, snapshot_path=snap).set_quiz(_quiz())
    s2 = QuizState(pr_number=7, snapshot_path=snap)
    assert s2.quiz is not None and s2.quiz.questions[0].id == "q1"


def test_replace_question_drops_old_answer(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    s.set_quiz(_quiz())  # question id "q1"
    s.record_answer("q1", "A")
    s.replace_question(0, MCQQuestion(id="q1b", prompt="p2", options=["X", "Y"], answer="Y"))
    assert "q1" not in s.answers          # old answer dropped
    assert s.quiz is not None and s.quiz.questions[0].id == "q1b"  # new question in place


def test_corrupt_snapshot_loads_as_empty(tmp_path: Path) -> None:
    snap = tmp_path / "s.json"
    snap.write_text("{not valid json")
    s = QuizState(pr_number=7, snapshot_path=snap)
    assert s.quiz is None and s.answers == {} and s.results is None


def test_set_results_stored_and_in_snapshot(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    s.set_quiz(_quiz())
    r = Results(pr_number=7, total_score=100,
                per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")])
    s.set_results(r)
    assert s.results is not None and s.results.total_score == 100
    snap = s.snapshot()
    results_data = snap["results"]
    assert isinstance(results_data, dict) and results_data["total_score"] == 100


def test_replace_question_out_of_range_raises(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    s.set_quiz(_quiz())
    with pytest.raises(IndexError):
        s.replace_question(5, MCQQuestion(id="x", prompt="p", options=["A", "B"], answer="A"))


def test_publishable_returns_none_until_graded(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    s.set_quiz(_quiz())
    assert s.publishable() is None
    s.set_results(Results(pr_number=7, total_score=100,
                          per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")]))
    snap = s.publishable()
    assert snap is not None and snap[0].questions[0].id == "q1" and snap[2].total_score == 100
