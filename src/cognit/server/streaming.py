"""Append-only activity log shared between the generation worker and `/progress`.

`cognit take` flips from generate-then-serve to serve-then-generate: the web
server starts first and generation runs on a background thread, emitting activity
events (Claude's text + step/tool labels) into a `Broker`. The browser polls
`GET /progress?cursor=N` and replays everything from its cursor — so refresh,
multi-tab, and reconnect all just work, and a terminal `error`/`ready` state can
never be "lost" by a late poller (it lives on the broker, not in the event feed).

Deliberately plain stdlib: a list guarded by a lock. No asyncio, no queue, no
event-loop bridging — the worker thread `emit`s, the request handler reads.
"""

from __future__ import annotations

import threading
from typing import Any

from cognit.engine.models import Quiz


class Broker:
    """Thread-safe, append-only event log plus terminal phase/quiz/error state."""

    def __init__(self, *, quiz: Quiz | None = None) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self.quiz: Quiz | None = quiz
        self.error: str | None = None
        # If a quiz is supplied up front (cache hit), there's nothing to generate.
        self.phase: str = "ready" if quiz is not None else "generating"

    def emit(self, event: dict[str, Any]) -> None:
        """Append one activity event. Called from the generation/grading worker thread."""
        with self._lock:
            self._events.append(event)

    def set_ready(self, quiz: Quiz) -> None:
        with self._lock:
            self.quiz = quiz
            self.phase = "ready"

    def set_error(self, message: str) -> None:
        with self._lock:
            self.error = message
            self.phase = "error"

    def snapshot(self, cursor: int) -> dict[str, Any]:
        """Return events from `cursor` onward plus current phase/quiz/error.

        `quiz` is included only once `phase == "ready"`, as a JSON-ready dict
        matching the inline `window.QUIZ` shape the frontend already renders.
        """
        with self._lock:
            return {
                "phase": self.phase,
                "events": self._events[cursor:],
                "next_cursor": len(self._events),
                "quiz": self.quiz.model_dump(mode="json")
                if self.phase == "ready" and self.quiz is not None
                else None,
                "error": self.error,
            }
