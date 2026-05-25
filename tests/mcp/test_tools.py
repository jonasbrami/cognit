from pathlib import Path

from cognit.engine.llm_fake import FakeLLM
from cognit.mcp import server as srv
from cognit.mcp.state import QuizState


def _draft():
    return {"version": "1", "questions": [
        {"type": "mcq", "id": "q1", "prompt": "p", "options": ["A", "B"],
         "answer": "A", "explanation": "because A"}]}


def _state(tmp_path: Path) -> QuizState:
    return QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")


def test_set_quiz_renders(tmp_path: Path):
    state = _state(tmp_path)
    out = srv.do_set_quiz(state, _draft())
    assert out["ok"] is True
    assert state.quiz is not None and state.quiz.questions[0].id == "q1"


def test_set_quiz_rejects_with_reasons(tmp_path: Path):
    state = _state(tmp_path)
    bad = {"version": "1", "questions": [{"type": "mcq", "id": "q1", "prompt": "p",
            "options": ["A", "B"], "answer": "A", "explanation": ""}]}
    out = srv.do_set_quiz(state, bad)
    assert out["ok"] is False
    assert any("explanation" in r for r in out["failures"])
    assert state.quiz is None


def test_replace_question(tmp_path: Path):
    state = _state(tmp_path)
    srv.do_set_quiz(state, _draft())
    new = {"type": "mcq", "id": "q1b", "prompt": "p2", "options": ["X", "Y"],
           "answer": "Y", "explanation": "because Y"}
    out = srv.do_replace_question(state, 0, new)
    assert out["ok"] is True
    assert state.quiz.questions[0].id == "q1b"


def test_grade(tmp_path: Path):
    state = _state(tmp_path)
    srv.do_set_quiz(state, _draft())
    state.record_answer("q1", "A")
    out = srv.do_grade(state, llm=FakeLLM())
    assert out["ok"] is True
    assert out["total_score"] == 100
    assert state.results is not None


def test_get_answers(tmp_path: Path):
    state = _state(tmp_path)
    srv.do_set_quiz(state, _draft())
    state.record_answer("q1", "A")
    out = srv.do_get_answers(state)
    assert out["answers"] == {"q1": "A"}
    assert out["quiz"]["questions"][0]["id"] == "q1"


def test_grade_without_quiz_returns_structured_failure(tmp_path: Path):
    out = srv.do_grade(_state(tmp_path), llm=FakeLLM())
    assert out["ok"] is False
    assert any("no quiz" in f for f in out["failures"])
