"""Authoritative session state for the MCP server: the quiz, the browser-collected
answers, and the last grading result. Write-through to a snapshot file on every
mutation so a crash/exit loses nothing and a fresh process rehydrates."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from cognit.engine.models import Question, Quiz, Results


class QuizState:
    """Session state for one PR review. Callers mutate only through the methods (which lock + persist); do not write the attributes directly."""

    def __init__(self, *, pr_number: int, snapshot_path: Path) -> None:
        self.pr_number = pr_number
        self._snapshot_path = snapshot_path
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.quiz: Quiz | None = None
        self.answers: dict[str, str] = {}
        self.results: Results | None = None
        self._load()

    def _load(self) -> None:
        if not self._snapshot_path.exists():
            return
        try:
            data = json.loads(self._snapshot_path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if data.get("quiz"):
            self.quiz = Quiz.model_validate(data["quiz"])
        self.answers = dict(data.get("answers") or {})
        if data.get("results"):
            self.results = Results.model_validate(data["results"])

    def _persist(self) -> None:
        payload = {
            "quiz": self.quiz.model_dump(mode="json") if self.quiz else None,
            "answers": self.answers,
            "results": self.results.model_dump(mode="json") if self.results else None,
        }
        self._snapshot_path.write_text(json.dumps(payload))

    def set_quiz(self, quiz: Quiz) -> None:
        with self._lock:
            self.quiz = quiz
            self.answers = {}
            self.results = None
            self._persist()

    def replace_question(self, index: int, question: Question) -> None:
        with self._lock:
            if self.quiz is None or not (0 <= index < len(self.quiz.questions)):
                raise IndexError(index)
            old_id = self.quiz.questions[index].id
            qs = list(self.quiz.questions)
            qs[index] = question
            self.quiz = self.quiz.model_copy(update={"questions": qs})
            self.answers.pop(old_id, None)
            self.results = None
            self._persist()

    def record_answer(self, question_id: str, value: str) -> None:
        with self._lock:
            self.answers[question_id] = value
            self._persist()

    def set_results(self, results: Results) -> None:
        with self._lock:
            self.results = results
            self._persist()

    def publishable(self) -> "tuple[Quiz, dict[str, str], Results] | None":
        """Atomically capture (quiz, answers, results) under the lock, or None if not
        yet gradeable. Avoids a TOCTOU where a concurrent set_quiz could null results
        between a caller's None-check and its read."""
        with self._lock:
            if self.quiz is None or self.results is None:
                return None
            return self.quiz, dict(self.answers), self.results

    def snapshot_for_grading(self) -> "tuple[Quiz, dict[str, str]] | None":
        """Atomically capture (quiz, answers-copy) under the lock for grading."""
        with self._lock:
            if self.quiz is None:
                return None
            return self.quiz, dict(self.answers)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "quiz": self.quiz.model_dump(mode="json") if self.quiz else None,
                "answers": dict(self.answers),
                "results": self.results.model_dump(mode="json") if self.results else None,
            }
