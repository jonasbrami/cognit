"""Tests for the Broker — the append-only activity log behind /progress.

The Broker is shared between the generation worker thread (which `emit`s events
and flips phase/quiz/error) and the request handlers (which read `snapshot`).
"""

import threading

from cognit.engine.models import MCQQuestion, Quiz
from cognit.server.streaming import Broker


def _quiz() -> Quiz:
    return Quiz(
        pr_number=7,
        questions=[MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A")],
    )


def test_new_broker_defaults_to_generating() -> None:
    b = Broker()
    snap = b.snapshot(0)
    assert snap["phase"] == "generating"
    assert snap["events"] == []
    assert snap["next_cursor"] == 0
    assert snap["quiz"] is None
    assert snap["error"] is None


def test_emit_appends_and_snapshot_slices_from_cursor() -> None:
    b = Broker()
    b.emit({"kind": "step", "tool": "submit_quiz"})
    b.emit({"kind": "text", "text": "thinking…", "tool": "submit_quiz"})

    first = b.snapshot(0)
    assert [e["kind"] for e in first["events"]] == ["step", "text"]
    assert first["next_cursor"] == 2

    # A poller that already saw both events sees nothing new.
    tail = b.snapshot(2)
    assert tail["events"] == []
    assert tail["next_cursor"] == 2

    # A poller mid-stream sees only the tail.
    mid = b.snapshot(1)
    assert [e["kind"] for e in mid["events"]] == ["text"]


def test_set_ready_exposes_quiz_only_when_ready() -> None:
    b = Broker()
    assert b.snapshot(0)["quiz"] is None
    b.set_ready(_quiz())
    snap = b.snapshot(0)
    assert snap["phase"] == "ready"
    assert snap["quiz"]["pr_number"] == 7
    assert snap["quiz"]["questions"][0]["id"] == "q1"


def test_set_error_sets_phase_and_message() -> None:
    b = Broker()
    b.set_error("claude binary not found")
    snap = b.snapshot(0)
    assert snap["phase"] == "error"
    assert snap["error"] == "claude binary not found"
    assert snap["quiz"] is None


def test_emit_is_thread_safe() -> None:
    b = Broker()

    def worker() -> None:
        for _ in range(200):
            b.emit({"kind": "text", "text": "x"})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert b.snapshot(0)["next_cursor"] == 8 * 200
