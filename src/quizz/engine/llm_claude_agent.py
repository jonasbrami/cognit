"""claude_agent_sdk-based LLM adapter for quizz.

Routes inference through the official `claude` binary (subprocessed by
claude_agent_sdk) so users on the Claude Code OAuth path can use sonnet/opus.
The direct Anthropic SDK + OAuth combo is gated by Anthropic to haiku only
(see docs/superpowers/specs/2026-05-22-claude-agent-sdk-engine-design.md).

The adapter implements the existing sync `LLMClient` Protocol by wrapping each
call in `asyncio.run`. Structured output is captured via in-process MCP tools:
the agent invokes a `submit_*` tool, the handler stuffs the validated args
into a closure-shared list, the adapter returns the args as a Pydantic model.
"""

from __future__ import annotations

import asyncio
from importlib import resources
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    create_sdk_mcp_server,
    query,
    tool,
)

from quizz.engine.llm import GenerateRequest
from quizz.engine.models import MermaidSet, MermaidSpec, QuizOutline

_TOOL_OUTLINE = "submit_quiz_outline"
_TOOL_MERMAID = "submit_mermaid_set"
_TOOL_GRADE = "submit_grade"


def _load_prompt(name: str) -> str:
    return resources.files("quizz.engine.prompts").joinpath(name).read_text()


def _format_files_blob(files: dict[str, str]) -> str:
    if not files:
        return ""
    return "\n".join(f'<file path="{p}">\n{c}\n</file>' for p, c in files.items())


def _format_misconceptions(misconceptions: list[str]) -> str:
    return "\n".join(f"- {m}" for m in misconceptions)


class ClaudeAgentLLM:
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model = model

    def _invoke_tool(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Spawn an agent, await one tool call, return the captured args or None."""
        raise NotImplementedError

    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        raise NotImplementedError

    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        raise NotImplementedError

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        raise NotImplementedError
