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
from collections.abc import Awaitable, Callable
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

_ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


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
        """Spawn an agent, await one tool call, return the captured args or None.

        Returns None if the agent finishes its turn without calling the MCP tool.
        Caller decides what to do (retry, raise, etc.).
        """
        captured: list[dict[str, Any]] = []

        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            captured.append(args)
            return {"content": [{"type": "text", "text": "ok"}]}

        decorated = tool(tool_name, tool_description, tool_schema)(handler)
        server = create_sdk_mcp_server(name="quizz", tools=[decorated])
        options = ClaudeAgentOptions(
            system_prompt=system,
            model=self._model,
            mcp_servers={"quizz": server},
            allowed_tools=[f"mcp__quizz__{tool_name}"],
            max_turns=2,
            permission_mode="bypassPermissions",
            setting_sources=[],
        )
        try:
            self._drain_agent(prompt=user, options=options, handler=handler)
        except CLINotFoundError as e:
            raise RuntimeError(
                "claude binary not found; install Claude Code "
                "(`npm i -g @anthropic-ai/claude-code`) or set ANTHROPIC_API_KEY"
            ) from e
        except (CLIConnectionError, ProcessError, ClaudeSDKError) as e:
            raise RuntimeError(f"claude agent SDK call failed: {e}") from e

        return captured[0] if captured else None

    def _drain_agent(
        self,
        *,
        prompt: str,
        options: ClaudeAgentOptions,
        handler: _ToolHandler,
    ) -> None:
        """Drain the SDK's `query` stream until the agent finishes its turn.

        The `handler` parameter is unused in production — the SDK invokes the
        registered MCP tool's handler internally when the agent calls the tool.
        It's passed in so tests can override `_drain_agent` and invoke the
        handler directly without spinning up a real `claude` subprocess.
        """
        del handler  # production-side: handler is fired by the SDK, not by us

        async def _drain() -> None:
            async for _ in query(prompt=prompt, options=options):
                pass

        asyncio.run(_drain())

    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        system = _load_prompt("system_generate.txt")
        user = _load_prompt("generate.txt").format(
            pr_title=req.pr_title,
            pr_body=req.pr_body,
            diff=req.diff,
            files=_format_files_blob(req.files),
        )
        args = self._invoke_tool(
            system=system,
            user=user,
            tool_name=_TOOL_OUTLINE,
            tool_description="Submit the generated quiz outline.",
            tool_schema=QuizOutline.model_json_schema(),
        )
        if args is None:
            raise RuntimeError(f"agent did not call {_TOOL_OUTLINE}")
        return QuizOutline.model_validate(args)

    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        system = _load_prompt("system_mermaid.txt")
        user = _load_prompt("mermaid.txt").format(
            diagram_type=spec.diagram_type,
            correct_description=spec.correct_description,
            misconceptions=_format_misconceptions(spec.misconceptions),
            style_notes=spec.style_notes,
        )
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "description": "Exactly four keys A, B, C, D mapping to mermaid sources.",
                    "properties": {
                        "A": {"type": "string"},
                        "B": {"type": "string"},
                        "C": {"type": "string"},
                        "D": {"type": "string"},
                    },
                    "required": ["A", "B", "C", "D"],
                    "additionalProperties": False,
                },
                "correct": {"type": "string", "enum": ["A", "B", "C", "D"]},
            },
            "required": ["options", "correct"],
            "additionalProperties": False,
        }
        args = self._invoke_tool(
            system=system,
            user=user,
            tool_name=_TOOL_MERMAID,
            tool_description="Submit 4 mermaid diagrams keyed A/B/C/D plus which is correct.",
            tool_schema=schema,
        )
        if args is None:
            raise RuntimeError(f"agent did not call {_TOOL_MERMAID}")
        return MermaidSet.model_validate(args)

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        system = _load_prompt("system_grade.txt")
        user = _load_prompt("grade_open.txt").format(
            prompt=question_prompt,
            rubric=rubric,
            answer=answer,
        )
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "feedback": {"type": "string"},
            },
            "required": ["score", "feedback"],
            "additionalProperties": False,
        }
        args = self._invoke_tool(
            system=system,
            user=user,
            tool_name=_TOOL_GRADE,
            tool_description="Submit a score and feedback for the open-ended answer.",
            tool_schema=schema,
        )
        if args is None:
            raise RuntimeError(f"agent did not call {_TOOL_GRADE}")
        score = max(0, min(100, int(args.get("score", 0))))
        return score, str(args.get("feedback", ""))
