"""_make_llm picks AnthropicLLM when ANTHROPIC_API_KEY is set, otherwise
ClaudeAgentLLM. This is the only public auth signal we honor."""

from __future__ import annotations

import pytest

from cognit.cli.take import _make_llm
from cognit.engine.llm_anthropic import AnthropicLLM
from cognit.engine.llm_claude_agent import ClaudeAgentLLM


def test_make_llm_uses_anthropic_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    llm = _make_llm("claude-sonnet-4-6")
    assert isinstance(llm, AnthropicLLM)


def test_make_llm_uses_claude_agent_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = _make_llm("claude-sonnet-4-6")
    assert isinstance(llm, ClaudeAgentLLM)
