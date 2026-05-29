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
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import TypeAdapter

from cognit.engine.llm import LLMClient
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import Question
from cognit.ghio.diff import fetch_pr_diff, split_diff, summarize_diff
from cognit.ghio.pr import post_comment as gh_post_comment
from cognit.mcp.grading import grade_state
from cognit.mcp.state import QuizState
from cognit.mcp.validate import validate_and_prepare, validate_question
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
    failures = validate_question(question)
    if failures:
        return {"ok": False, "failures": failures}
    parsed: Question = TypeAdapter(Question).validate_python(question)
    try:
        state.replace_question(index, parsed)
    except IndexError:
        n = len(state.quiz.questions) if state.quiz else 0
        return {"ok": False, "failures": [f"index {index} out of range (have {n})"]}
    return {"ok": True}


def do_get_answers(state: QuizState) -> dict[str, Any]:
    snap = state.snapshot()
    return {"answers": snap["answers"], "confidences": snap["confidences"], "quiz": snap["quiz"]}


def do_grade(state: QuizState, *, llm: LLMClient) -> dict[str, Any]:
    try:
        results = grade_state(state, llm=llm)
    except RuntimeError as e:
        return {"ok": False, "failures": [str(e)]}
    return {"ok": True, **results.model_dump()}


def do_file_diff(path: str, sections: dict[str, str]) -> str:
    """Return the diff section for ONE changed file, tolerating basename and
    repo-relative-suffix variants. Suffix matches only at path boundaries (so "a.py"
    does NOT match "src/ya.py"), and refuses to guess when >1 file matches."""
    path = path.strip()
    section = sections.get(path) if path else None
    if section is None and path:
        hits = [p for p in sections if p.endswith("/" + path) or p.rsplit("/", 1)[-1] == path]
        section = sections[hits[0]] if len(hits) == 1 else None
    if section is None:
        listing = ", ".join(sorted(sections)) or "(none)"
        return f"No changed file matches {path!r}. Changed files: {listing}"
    return section


class _DiffProvider:
    """Lazily fetch + cache the PR's filtered diff once per process (thread-safe)."""

    def __init__(self, pr_url: str) -> None:
        self._pr_url = pr_url
        self._lock = threading.Lock()
        self._sections: dict[str, str] | None = None
        self._raw: str | None = None

    def _ensure(self) -> None:
        with self._lock:
            if self._raw is None:
                raw = fetch_pr_diff(self._pr_url)
                sections = split_diff(raw)
                self._raw, self._sections = raw, sections  # assign only after both succeed

    def sections(self) -> dict[str, str]:
        self._ensure()
        assert self._sections is not None
        return self._sections

    def overview(self) -> str:
        self._ensure()
        assert self._raw is not None
        return summarize_diff(self._raw)


# ── process wiring ───────────────────────────────────────────────────────────


def _build_mcp(state: QuizState, llm: LLMClient, diffs: _DiffProvider) -> FastMCP:
    mcp = FastMCP("cognit")

    @mcp.tool()
    async def set_quiz(quiz: dict[str, Any]) -> dict[str, Any]:
        """Render/replace the whole quiz in the browser. `quiz` is {version, questions:[...]}.
        Per-question shapes (note the `answer` field differs by type): mcq -> options is a
        list of strings, answer is the full option text; mermaid -> options keyed A/B/C/D,
        answer is the key; tf -> answer is a boolean; open -> needs a rubric, no answer.
        Rejected (with reasons to fix) if any mermaid set is invalid/non-uniform/non-distinct
        or any question lacks an explanation. A rejection renders NOTHING — the browser is
        unchanged — so fix the reasons and resubmit the whole quiz; iterating is free."""
        return await asyncio.to_thread(do_set_quiz, state, quiz)

    @mcp.tool()
    def replace_question(index: int, question: dict[str, Any]) -> dict[str, Any]:
        """Replace the 0-based question at `index` (skip-and-replace)."""
        return do_replace_question(state, index, question)

    @mcp.tool()
    def get_answers() -> dict[str, Any]:
        """Read back what the developer did in the browser: their `answers`, their
        per-question `confidences` (1–5 self-ratings, where given), and the current quiz.
        Confidence vs. correctness is useful for follow-ups — e.g. probe again where they
        were confident but wrong, or drill a concept they were unsure about."""
        return do_get_answers(state)

    @mcp.tool()
    async def grade() -> dict[str, Any]:
        """Grade the current answers (deterministic + strict open grading) and show the
        scorecard in the browser. Supply no judgments — scoring is computed here."""
        return await asyncio.to_thread(do_grade, state, llm=llm)

    @mcp.tool()
    def changed_files() -> str:
        """List the PR's changed files with +/- line counts. Call this first, then
        file_diff(path) for the files worth quizzing."""
        return diffs.overview()

    @mcp.tool()
    def file_diff(path: str) -> str:
        """Fetch the diff hunks for ONE changed file (a path from changed_files)."""
        return do_file_diff(path, diffs.sections())

    return mcp


def _start_web(
    state: QuizState,
    *,
    llm: LLMClient,
    pr_url: str,
    port: int,
    diffs: _DiffProvider,
    branch: str,
) -> None:
    app = build_web_app(
        state,
        post_comment=lambda body: gh_post_comment(pr_url, body),
        grade=lambda: grade_state(state, llm=llm),
        diff_section=lambda path: do_file_diff(path, diffs.sections()),
        changed_files=lambda: list(diffs.sections()),
        pr_url=pr_url,
        branch=branch,
    )
    url = f"http://127.0.0.1:{port}"

    def _open() -> None:
        import time

        time.sleep(1.0)  # give uvicorn a moment to bind
        try:
            webbrowser.open(url)
        except Exception:  # headless / no browser — non-fatal
            logger.debug("could not open browser at %s", url)

    threading.Thread(target=_open, daemon=True).start()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    try:
        uvicorn.Server(cfg).run()
    except Exception:
        logger.exception("cognit web host on port %d exited unexpectedly", port)
        raise


def _configure_logging() -> None:
    """Emit the package's logs at COGNIT_LOG_LEVEL (default WARNING) to stderr.

    Never stdout: this process speaks MCP JSON-RPC over stdout, so any stray write
    there corrupts the protocol. claude captures our stderr to disk under
    ~/.cache/claude-cli-nodejs/.../mcp-logs-cognit/, making DEBUG runs troubleshootable.
    """
    level_name = os.environ.get("COGNIT_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.WARNING),
        format="%(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def main() -> None:
    _configure_logging()
    pr_url = os.environ["COGNIT_PR_URL"]
    pr_number = int(os.environ["COGNIT_PR_NUMBER"])
    port = int(os.environ["COGNIT_HTTP_PORT"])
    snapshot = Path(os.environ["COGNIT_SNAPSHOT_PATH"])
    branch = os.environ.get("COGNIT_BRANCH", "")

    state = QuizState(pr_number=pr_number, snapshot_path=snapshot)
    # Best-effort early collision detection: probe the port so an obvious conflict
    # fails fast here rather than in the daemon thread. Not an atomic reservation —
    # uvicorn rebinds below (a tiny TOCTOU window remains; acceptable for a local,
    # single-user, short-lived session).
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))

    print(f"cognit quiz: http://127.0.0.1:{port}", file=sys.stderr)
    llm: LLMClient = ClaudeAgentLLM()
    diffs = _DiffProvider(pr_url)  # one fetch/cache shared by the MCP tools and the web /diff
    threading.Thread(
        target=_start_web,
        args=(state,),
        kwargs={"llm": llm, "pr_url": pr_url, "port": port, "diffs": diffs, "branch": branch},
        daemon=True,
    ).start()
    _build_mcp(state, llm, diffs).run()
