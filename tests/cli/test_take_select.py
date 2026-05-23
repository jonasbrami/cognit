"""`_make_llm` always returns the OAuth-only ClaudeAgentLLM. The direct Anthropic
API-key path was removed, so `ANTHROPIC_API_KEY` is no longer consulted."""

from __future__ import annotations

import pytest

from cognit.cli.take import _make_llm
from cognit.engine.llm_claude_agent import ClaudeAgentLLM


def test_make_llm_is_claude_agent_even_with_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert isinstance(_make_llm("claude-sonnet-4-6"), ClaudeAgentLLM)
