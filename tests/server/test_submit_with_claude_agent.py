"""Regression test: POST /submit must work when the LLM is ClaudeAgentLLM.

ClaudeAgentLLM._drain_agent calls `asyncio.run(...)` to drive the SDK's async
`query` generator from the sync `LLMClient` Protocol. That works when the
adapter is called from a plain sync context (cli/take.py) but Python forbids
`asyncio.run()` from inside an already-running event loop — which is exactly
what uvicorn / FastAPI's TestClient provides for an `async def` route.

This test reproduces that scenario by:
  - monkey-patching `_drain_agent` so it doesn't spawn a real `claude`
    subprocess, but DOES preserve the `asyncio.run(handler(...))` pattern
    that triggers the bug;
  - POSTing answers (with one OpenQuestion) to /submit through TestClient,
    which runs the route on a real event loop.

Without the fix, this fails with a 500 / loop-in-loop RuntimeError. With the
fix (submit handler offloads `grade(...)` to `asyncio.to_thread`), it returns
200 and the canned grade round-trips back.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import MCQQuestion, OpenQuestion, Quiz
from cognit.server.app import build_app


def test_submit_with_claude_agent_llm_does_not_loop_in_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/submit must not crash when the LLM calls asyncio.run internally.

    Asserts:
      - the response is 200 (route did not blow up),
      - the OpenQuestion was graded with the canned score/feedback that the
        patched _drain_agent fed back through the handler.
    """
    canned = {"score": 80, "feedback": "ok"}

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        # CRITICAL: keep the asyncio.run call. This is the exact production
        # pattern that triggers the loop-in-loop bug when /submit runs on
        # uvicorn's event loop.
        asyncio.run(handler(canned))

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)

    quiz = Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A"),
            OpenQuestion(id="q2", prompt="explain", rubric="must mention X"),
        ],
    )

    app = build_app(
        quiz=quiz,
        pr_url="https://github.com/o/r/pull/42",
        llm=ClaudeAgentLLM(),
        post_comment=lambda md: "https://x/y#unused",
    )
    client = TestClient(app)
    payload = {
        "version": "1",
        "pr_number": 42,
        "entries": [
            {"question_id": "q1", "value": "A"},
            {"question_id": "q2", "value": "because X is important"},
        ],
    }
    r = client.post("/submit", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    by_id = {q["question_id"]: q for q in data["per_question"]}
    assert by_id["q2"]["score"] == 80
    assert by_id["q2"]["feedback"] == "ok"


def test_submit_streams_grading_activity_to_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grading the open question must stream activity into the broker so the
    browser's grading overlay (polling /progress) shows it."""
    canned = {"score": 90, "feedback": "good"}

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        # Simulate Claude narrating while grading, then submitting the score.
        if self.on_event is not None:
            self.on_event({"kind": "text", "text": "weighing the answer…", "tool": self._current_tool})
        asyncio.run(handler(canned))

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)

    quiz = Quiz(
        pr_number=42,
        questions=[OpenQuestion(id="q1", prompt="explain", rubric="mention X")],
    )
    app = build_app(
        quiz=quiz,
        pr_url="https://github.com/o/r/pull/42",
        llm=ClaudeAgentLLM(),
        post_comment=lambda md: "https://x/y#unused",
    )
    client = TestClient(app)
    r = client.post(
        "/submit",
        json={
            "version": "1",
            "pr_number": 42,
            "entries": [{"question_id": "q1", "value": "X matters"}],
        },
    )
    assert r.status_code == 200, r.text

    events = client.get("/progress?cursor=0").json()["events"]
    # The grade tool announces a step, then Claude's text streams — both tagged submit_grade.
    assert {"kind": "step", "tool": "submit_grade"} in events
    assert any(
        e["kind"] == "text" and e["tool"] == "submit_grade" and "weighing" in e["text"]
        for e in events
    )
