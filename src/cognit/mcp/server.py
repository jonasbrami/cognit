"""The MCP surface (FastMCP stdio tools) + process wiring.

Tools are thin: each `do_*` function holds the pure logic (unit-tested directly); the
`@mcp.tool()` wrappers just adapt args/results. `main()` reads env (port/snapshot/PR),
starts the FastAPI browser host on a daemon thread (fail-hard on bind conflict so a
session never silently attaches to another), then runs the stdio MCP loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import TypeAdapter, ValidationError

from cognit.engine.llm import LLMClient
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import Question
from cognit.ghio.pr import post_comment as gh_post_comment
from cognit.mcp.grading import grade_state
from cognit.mcp.state import QuizState
from cognit.mcp.validate import validate_and_prepare
from cognit.mcp.web import build_web_app

logger = logging.getLogger("cognit.mcp.server")

# ── pure tool logic (unit-tested) ────────────────────────────────────────────


def do_set_quiz(state: QuizState, draft: dict[str, Any]) -> dict[str, Any]:
    quiz, failures = validate_and_prepare(draft, pr_number=state.pr_number)
    if quiz is None:
        return {"ok": False, "failures": failures}
    state.set_quiz(quiz)
    return {"ok": True, "rendered": len(quiz.questions)}


def do_replace_question(state: QuizState, index: int, question: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed: Question = TypeAdapter(Question).validate_python(question)
    except ValidationError as e:
        return {"ok": False, "failures": [f"malformed question: {e.errors()}"]}
    try:
        state.replace_question(index, parsed)
    except IndexError:
        n = len(state.quiz.questions) if state.quiz else 0
        return {"ok": False, "failures": [f"index {index} out of range (have {n})"]}
    return {"ok": True}


def do_get_answers(state: QuizState) -> dict[str, Any]:
    snap = state.snapshot()
    return {"answers": snap["answers"], "quiz": snap["quiz"]}


def do_grade(state: QuizState, *, llm: LLMClient) -> dict[str, Any]:
    try:
        results = grade_state(state, llm=llm)
    except RuntimeError as e:
        return {"ok": False, "failures": [str(e)]}
    return {"ok": True, **results.model_dump()}


# ── process wiring ───────────────────────────────────────────────────────────


def _build_mcp(state: QuizState, llm: LLMClient) -> FastMCP:
    mcp = FastMCP("cognit")

    @mcp.tool()
    async def set_quiz(quiz: dict[str, Any]) -> dict[str, Any]:
        """Render/replace the whole quiz in the browser. `quiz` is {version, questions:[...]}.
        Rejected (with reasons to fix) if any mermaid set is invalid/non-uniform/non-distinct
        or any question lacks an explanation."""
        return await asyncio.to_thread(do_set_quiz, state, quiz)

    @mcp.tool()
    def replace_question(index: int, question: dict[str, Any]) -> dict[str, Any]:
        """Replace the 0-based question at `index` (skip-and-replace)."""
        return do_replace_question(state, index, question)

    @mcp.tool()
    def get_answers() -> dict[str, Any]:
        """Read back the answers the developer selected in the browser + the current quiz."""
        return do_get_answers(state)

    @mcp.tool()
    async def grade() -> dict[str, Any]:
        """Grade the current answers (deterministic + strict open grading) and show the
        scorecard in the browser. Supply no judgments — scoring is computed here."""
        return await asyncio.to_thread(do_grade, state, llm=llm)

    return mcp


def _start_web(state: QuizState, *, pr_url: str, port: int) -> None:
    app = build_web_app(state, post_comment=lambda body: gh_post_comment(pr_url, body))
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    try:
        uvicorn.Server(cfg).run()
    except Exception:
        logger.exception("cognit web host on port %d exited unexpectedly", port)
        raise


def main() -> None:
    pr_url = os.environ["COGNIT_PR_URL"]
    pr_number = int(os.environ["COGNIT_PR_NUMBER"])
    port = int(os.environ["COGNIT_HTTP_PORT"])
    snapshot = Path(os.environ["COGNIT_SNAPSHOT_PATH"])

    state = QuizState(pr_number=pr_number, snapshot_path=snapshot)
    # Best-effort early collision detection: probe the port so an obvious conflict
    # fails fast here rather than in the daemon thread. Not an atomic reservation —
    # uvicorn rebinds below (a tiny TOCTOU window remains; acceptable for a local,
    # single-user, short-lived session).
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))

    threading.Thread(
        target=_start_web, args=(state,), kwargs={"pr_url": pr_url, "port": port}, daemon=True
    ).start()
    llm: LLMClient = ClaudeAgentLLM()
    _build_mcp(state, llm).run()
