"""Tests for the ClaudeAgentLLM adapter.

Mocking seam: `ClaudeAgentLLM._drain_agent`. Production's `_drain_agent` ignores
its `handler` argument (the SDK fires the registered MCP handler internally); tests
override it to inject canned tool-call args without spawning a real `claude`
subprocess. The `options` it receives let tests assert the exact tool restrictions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_agent_sdk import CLINotFoundError

import cognit.engine.llm_claude_agent as llm_mod
from cognit.engine.llm import GenerateRequest
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import MCQQuestion, QuizDraft


def _req() -> GenerateRequest:
    return GenerateRequest(
        pr_title="t",
        pr_body="b",
        pr_number=7,
        pr_url="https://github.com/o/r/pull/7",
        branch="feat/x",
    )


def _make_drain_that_calls_handler(args: dict[str, Any]) -> Any:
    def fake(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        asyncio.run(handler(args))

    return fake


def _make_drain_that_does_nothing() -> Any:
    def fake(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        return None

    return fake


# --- _invoke_tool (single-tool path: mermaid + grading) ---


def test_invoke_tool_returns_captured_args(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = {"foo": "bar", "n": 42}
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_calls_handler(canned))
    llm = ClaudeAgentLLM()
    result = llm._invoke_tool(
        system="sys",
        user="usr",
        tool_name="my_tool",
        tool_description="desc",
        tool_schema={"type": "object", "properties": {"foo": {"type": "string"}}},
    )
    assert result == canned


def test_invoke_tool_returns_none_when_handler_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_does_nothing())
    llm = ClaudeAgentLLM()
    result = llm._invoke_tool(
        system="sys",
        user="usr",
        tool_name="my_tool",
        tool_description="desc",
        tool_schema={"type": "object"},
    )
    assert result is None


def test_invoke_tool_disables_all_builtin_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mermaid/grading agents need only their one MCP submit tool. `tools=[]` disables
    ALL built-in tools (Bash/Write/Edit/...) — load-bearing because permission_mode is
    bypassPermissions, which would otherwise auto-approve every built-in tool."""
    captured_options: list[Any] = []

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        captured_options.append(options)

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    llm = ClaudeAgentLLM(model="claude-opus-4-7")
    llm._invoke_tool(
        system="my-system",
        user="my-user",
        tool_name="my_tool",
        tool_description="my desc",
        tool_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
    )
    assert len(captured_options) == 1
    opts = captured_options[0]
    assert opts.system_prompt == "my-system"
    assert opts.model == "claude-opus-4-7"
    assert opts.tools == []  # the real restriction
    assert opts.allowed_tools == ["mcp__cognit__my_tool"]
    assert "cognit" in opts.mcp_servers
    assert opts.permission_mode == "bypassPermissions"


def test_invoke_tool_maps_cli_not_found_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        raise CLINotFoundError("claude binary not on PATH")

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="claude binary not found"):
        llm._invoke_tool(
            system="s",
            user="u",
            tool_name="t",
            tool_description="d",
            tool_schema={"type": "object"},
        )


# --- draft_quiz (agentic, read-only multi-tool path) ---


def test_draft_quiz_restricts_to_readonly_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generation agent gets read-only built-ins via `tools=` (the availability knob),
    a cwd at the repo root, and a higher turn budget. It must NOT be able to write/shell."""
    canned = QuizDraft(questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")])
    seen: dict[str, Any] = {}

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        seen["options"] = options
        seen["prompt"] = prompt
        asyncio.run(handler(canned.model_dump()))  # agent submits the quiz

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo/root")

    llm = ClaudeAgentLLM()
    out = llm.draft_quiz(_req())

    assert out == canned
    opts = seen["options"]
    assert opts.tools == ["Read", "Grep", "Glob"]  # only read-only built-ins available
    assert opts.allowed_tools == [
        "Read",
        "Grep",
        "Glob",
        "mcp__cognit__pr_overview",
        "mcp__cognit__file_diff",
        "mcp__cognit__submit_quiz",
    ]
    assert opts.cwd == "/repo/root"
    assert opts.max_turns == 30
    assert opts.permission_mode == "bypassPermissions"
    # Two PreToolUse matchers: read-confinement, then submit-validation.
    assert [m.matcher for m in opts.hooks["PreToolUse"]] == [
        "Read|Grep|Glob",
        "mcp__cognit__submit_quiz",
    ]
    # PR context reaches the prompt.
    assert "feat/x" in seen["prompt"]


def test_read_confinement_hook_denies_paths_outside_repo() -> None:
    """The PreToolUse read-confinement hook denies absolute/`..`-escaping reads and
    allows paths inside the repo (relative paths resolve against the root)."""
    matcher = llm_mod._read_confinement_hook("/repo/root")
    assert matcher.matcher == "Read|Grep|Glob"
    hook = matcher.hooks[0]

    def run(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, None, {}))

    def denied(out: dict[str, Any]) -> bool:
        return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    # Outside the repo (absolute) -> denied.
    assert denied(run("Read", {"file_path": "/etc/passwd"}))
    assert denied(run("Read", {"file_path": "/home/someone/.ssh/id_rsa"}))
    # Relative path escaping the repo -> denied.
    assert denied(run("Read", {"file_path": "../../etc/passwd"}))
    assert denied(run("Grep", {"path": "/repo/other"}))
    # Inside the repo -> allowed (no decision emitted).
    assert not denied(run("Read", {"file_path": "src/app.py"}))
    assert not denied(run("Read", {"file_path": "/repo/root/src/app.py"}))
    # No path given (Grep/Glob default to cwd) -> allowed.
    assert not denied(run("Glob", {"pattern": "**/*.py"}))


_FAKE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-old\n+new\n+extra\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-x\n+y\n"
)


def test_draft_quiz_registers_overview_file_diff_and_submit_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three MCP tools are registered: `pr_overview` (stat), `file_diff` (one file's
    hunks on demand), and the terminal `submit_quiz`. The diff is fetched once and
    served piecewise — never as one blob."""
    recorded: dict[str, Any] = {}
    real_create = llm_mod.create_sdk_mcp_server

    def spy_create(*args: Any, **kwargs: Any) -> Any:
        recorded["tools"] = kwargs["tools"]
        return real_create(*args, **kwargs)

    canned = QuizDraft(questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")])

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        asyncio.run(handler(canned.model_dump()))

    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        return _FAKE_DIFF

    monkeypatch.setattr(llm_mod, "create_sdk_mcp_server", spy_create)
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(llm_mod, "fetch_pr_diff", fake_fetch)

    llm = ClaudeAgentLLM()
    out = llm.draft_quiz(_req())
    assert out == canned

    tools = {t.name: t for t in recorded["tools"]}
    assert list(tools) == ["pr_overview", "file_diff", "submit_quiz"]

    # pr_overview returns the stat (files + counts), not the raw diff.
    overview = asyncio.run(tools["pr_overview"].handler({}))["content"][0]["text"]
    assert "src/a.py | +2 -1" in overview
    assert "src/b.py | +1 -1" in overview
    assert "@@" not in overview  # it's a summary, not hunks

    # file_diff returns one file's hunks; basename match is tolerated.
    sect = asyncio.run(tools["file_diff"].handler({"path": "a.py"}))["content"][0]["text"]
    assert "diff --git a/src/a.py" in sect and "+extra" in sect
    assert "src/b.py" not in sect

    # An unknown path lists what's available rather than erroring.
    miss = asyncio.run(tools["file_diff"].handler({"path": "nope.py"}))["content"][0]["text"]
    assert "No changed file matches" in miss

    # The diff is fetched once and reused across overview + file_diff calls.
    assert len(fetched) == 1


def test_draft_quiz_raises_when_submit_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_does_nothing())
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo")
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_quiz"):
        llm.draft_quiz(_req())


# --- grade_open ---


def test_grade_open_calls_grade_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_invoke(self: ClaudeAgentLLM, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"score": 75, "feedback": "good"}

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    score, fb = llm.grade_open("why?", "must mention X", "because")
    assert (score, fb) == (75, "good")
    assert seen["tool_name"] == "submit_grade"


def test_grade_open_clamps_score(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_invoke(self: ClaudeAgentLLM, **kw: Any) -> dict[str, Any]:
        return {"score": 150, "feedback": "fine"}

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    score, _ = llm.grade_open("why?", "r", "a")
    assert score == 100


def test_grade_open_raises_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", lambda self, **kw: None)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_grade"):
        llm.grade_open("why?", "r", "a")
