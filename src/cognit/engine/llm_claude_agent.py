"""claude_agent_sdk-based LLM adapter for cognit.

Routes inference through the official `claude` binary (subprocessed by
claude_agent_sdk), the only path that lets Claude Code OAuth users reach
sonnet/opus (the direct Anthropic SDK + OAuth combo is gated to haiku). This is
now the sole inference path; the direct-API adapter was removed.

Structured output is captured via in-process MCP tools: the agent invokes a
`submit_*` tool, the handler stuffs the validated args into a closure-shared
list, and the adapter returns them as a Pydantic model.

Tool restriction (load-bearing): `permission_mode="bypassPermissions"` auto-runs
every *available* tool without prompting, so availability — not the allow-list —
is what keeps an agent safe. We restrict availability with the SDK `tools`
parameter (CLI `--tools`): the single-tool paths (mermaid/grading) pass
`tools=[]` (no built-in tools at all). `allowed_tools` only auto-approves; it
does not gate availability.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

_TOOL_GRADE = "submit_grade"

_INVOKE_MAX_TURNS = 8

_ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _load_prompt(name: str) -> str:
    return resources.files("cognit.engine.prompts").joinpath(name).read_text()


class ClaudeAgentLLM:
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model = model
        # Optional activity sink. When set (by `cognit take` during grading),
        # `_drain_agent` forwards Claude's text and tool calls here instead of
        # discarding them. Kept off the LLMClient Protocol — only this adapter
        # emits activity; other implementers (the test FakeLLM) just never set it.
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self._current_tool: str = ""

    def _run_agent(
        self,
        *,
        system: str,
        user: str,
        server: Any,
        allowed_tools: list[str],
        tools: list[str],
        max_turns: int,
        cwd: str | None,
        handler: _ToolHandler,
        hooks: Any = None,
    ) -> None:
        """Build options and drive the SDK, mapping every failure to RuntimeError.

        `tools` is the availability restriction (CLI `--tools`); `allowed_tools` only
        auto-approves. The RuntimeError mapping is load-bearing — callers rely on a
        single error type here.
        """
        options = ClaudeAgentOptions(
            system_prompt=system,
            model=self._model,
            mcp_servers={"cognit": server},
            tools=tools,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            cwd=cwd,
            permission_mode="bypassPermissions",
            setting_sources=[],
            hooks=hooks,
        )
        try:
            self._drain_agent(prompt=user, options=options, handler=handler)
        except CLINotFoundError as e:
            raise RuntimeError(
                "claude binary not found; install Claude Code "
                "(`npm i -g @anthropic-ai/claude-code`) and run `claude login`"
            ) from e
        except (CLIConnectionError, ProcessError, ClaudeSDKError) as e:
            raise RuntimeError(f"claude agent SDK call failed: {e}") from e
        except Exception as e:
            # The SDK raises bare `Exception` for protocol-level errors like
            # "Reached maximum number of turns" — wrap to keep callers' catch uniform.
            raise RuntimeError(f"claude agent SDK error: {e}") from e

    def _invoke_tool(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Spawn a single-tool agent, await one tool call, return the captured args or None.

        No built-in tools (`tools=[]`): the agent's only job is to call the one MCP
        submit tool. Returns None if the agent finishes its turn without calling it.
        """
        captured: list[dict[str, Any]] = []

        # Tag every activity event from this invocation with its tool, and
        # announce the phase start so the feed reads "generating outline" etc.
        self._current_tool = tool_name
        if self.on_event is not None:
            self.on_event({"kind": "step", "tool": tool_name})

        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            captured.append(args)
            return {"content": [{"type": "text", "text": "ok"}]}

        decorated = tool(tool_name, tool_description, tool_schema)(handler)
        server = create_sdk_mcp_server(name="cognit", tools=[decorated])
        self._run_agent(
            system=system,
            user=user,
            server=server,
            allowed_tools=[f"mcp__cognit__{tool_name}"],
            tools=[],
            max_turns=_INVOKE_MAX_TURNS,
            cwd=None,
            handler=handler,
        )
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
            async for msg in query(prompt=prompt, options=options):
                self._forward_activity(msg)

        asyncio.run(_drain())

    def _forward_activity(self, msg: Any) -> None:
        """Forward an assistant message's reasoning, prose, and tool calls to `self.on_event`.

        No-op unless a sink is attached. Tool results and non-assistant messages
        (results/system) are ignored. Thinking blocks ARE forwarded (as `thinking`
        events) so the live feed shows Claude's reasoning during the long silent
        stretches between tool calls — otherwise the feed looks frozen while the
        agent reads the diff and decides what to inspect.
        """
        if self.on_event is None or not isinstance(msg, AssistantMessage):
            return
        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                self.on_event(
                    {"kind": "thinking", "text": block.thinking, "tool": self._current_tool}
                )
            elif isinstance(block, TextBlock):
                self.on_event({"kind": "text", "text": block.text, "tool": self._current_tool})
            elif isinstance(block, ToolUseBlock):
                event = {"kind": "tool_use", "name": block.name, "tool": self._current_tool}
                # Surface the most informative argument so the feed shows WHICH file/pattern
                # the agent is inspecting (e.g. "Read mermaid.py"), not just the tool name.
                args = block.input if isinstance(block.input, dict) else {}
                detail = args.get("file_path") or args.get("path") or args.get("pattern")
                if detail:
                    event["detail"] = str(detail)
                self.on_event(event)

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
