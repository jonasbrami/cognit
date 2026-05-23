"""Tests for the activity sink in ClaudeAgentLLM._drain_agent.

Production's `_drain_agent` drains `claude_agent_sdk.query(...)`. When `self.on_event`
is set, it must forward each assistant `TextBlock` as a `text` event and each
`ToolUseBlock` as a `tool_use` event, tagged with the current tool, and ignore
thinking blocks and non-assistant messages. We mock `query` (the module-level
import) to yield canned SDK messages without spawning a real `claude` subprocess.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock

from cognit.engine.llm_claude_agent import ClaudeAgentLLM


def _fake_query_yielding(*messages: Any) -> Any:
    async def _q(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        for m in messages:
            yield m

    return _q


def test_drain_agent_forwards_text_and_tool_use_events(monkeypatch: pytest.MonkeyPatch) -> None:
    msgs = [
        AssistantMessage(
            content=[
                TextBlock(text="picking questions…"),
                ThinkingBlock(thinking="secret reasoning", signature="sig"),
                ToolUseBlock(id="t1", name="Read", input={}),
            ],
            model="claude-sonnet-4-6",
        ),
        object(),  # a non-AssistantMessage (e.g. ResultMessage) must be ignored
    ]
    monkeypatch.setattr("cognit.engine.llm_claude_agent.query", _fake_query_yielding(*msgs))
    captured: list[dict[str, Any]] = []
    llm = ClaudeAgentLLM()
    llm.on_event = captured.append
    llm._current_tool = "submit_quiz_outline"

    llm._drain_agent(prompt="u", options=None, handler=None)

    assert captured == [
        {"kind": "text", "text": "picking questions…", "tool": "submit_quiz_outline"},
        {"kind": "tool_use", "name": "Read", "tool": "submit_quiz_outline"},
    ]


def test_drain_agent_is_silent_without_a_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    msgs = [AssistantMessage(content=[TextBlock(text="hi")], model="m")]
    monkeypatch.setattr("cognit.engine.llm_claude_agent.query", _fake_query_yielding(*msgs))
    llm = ClaudeAgentLLM()  # on_event defaults to None
    # Must drain without raising and without requiring a sink.
    llm._drain_agent(prompt="u", options=None, handler=None)
