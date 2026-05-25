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

from cognit.engine.llm_claude_agent import ClaudeAgentLLM


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
