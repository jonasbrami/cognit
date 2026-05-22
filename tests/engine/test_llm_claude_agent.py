"""Tests for the ClaudeAgentLLM adapter.

Two layers of mocking:
  - `ClaudeAgentLLM._drain_agent` for the per-method tests, since the SDK
    plumbing is the same for every method (the per-method tests only care
    about which schema and prompts get passed).
  - `claude_agent_sdk.query` (via the module-level import in
    `quizz.engine.llm_claude_agent`) for end-to-end tests of `_invoke_tool`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from claude_agent_sdk import CLINotFoundError

from quizz.engine.llm import GenerateRequest
from quizz.engine.llm_claude_agent import ClaudeAgentLLM
from quizz.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizOutline


def _make_drain_that_calls_handler(args: dict[str, Any]) -> Any:
    """Return a fake `_drain_agent` that immediately invokes the captured handler.

    Production's `_drain_agent` ignores the `handler` argument — the SDK fires it
    internally. Tests use this seam to inject canned tool-call args without
    spawning a real `claude` subprocess.
    """

    def fake(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        asyncio.run(handler(args))

    return fake


def _make_drain_that_does_nothing() -> Any:
    """Fake `_drain_agent` that returns without firing the handler — simulates
    the agent chatting without calling the MCP tool."""

    def fake(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        return None

    return fake


# --- _invoke_tool ---


def test_invoke_tool_returns_captured_args(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = {"foo": "bar", "n": 42}
    monkeypatch.setattr(
        ClaudeAgentLLM,
        "_drain_agent",
        _make_drain_that_calls_handler(canned),
    )
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


def test_invoke_tool_builds_options_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP tool is registered with the right name, schema, and allowlist."""
    captured_options: list[Any] = []

    def fake_drain(
        self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any
    ) -> None:
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
    assert opts.allowed_tools == ["mcp__quizz__my_tool"]
    assert "quizz" in opts.mcp_servers
    assert opts.permission_mode == "bypassPermissions"


def test_invoke_tool_maps_cli_not_found_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_drain(
        self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any
    ) -> None:
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


# --- generate_quiz_outline ---


def test_generate_quiz_outline_calls_outline_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    seen: dict[str, Any] = {}

    def fake_invoke(self: ClaudeAgentLLM, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return canned.model_dump()

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    out = llm.generate_quiz_outline(
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={})
    )
    assert out == canned
    assert seen["tool_name"] == "submit_quiz_outline"
    # The outline schema must define MermaidPlaceholder (the pre-render type) and
    # NOT MermaidQuestion (the post-render type). Checking $defs avoids false
    # positives from docstrings.
    defs = seen["tool_schema"].get("$defs", {})
    assert "MermaidPlaceholder" in defs
    assert "MermaidQuestion" not in defs
    # System prompt should come from system_generate.txt.
    assert "comprehension quiz author" in seen["system"].lower()


def test_generate_quiz_outline_raises_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", lambda self, **kw: None)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_quiz_outline"):
        llm.generate_quiz_outline(
            GenerateRequest(diff="x", pr_title="t", pr_body="b", files={})
        )
