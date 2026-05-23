"""claude_agent_sdk-based LLM adapter for cognit.

Routes inference through the official `claude` binary (subprocessed by
claude_agent_sdk), the only path that lets Claude Code OAuth users reach
sonnet/opus (the direct Anthropic SDK + OAuth combo is gated to haiku — see
docs/superpowers/specs/2026-05-22-claude-agent-sdk-engine-design.md). This is
now the sole inference path; the direct-API adapter was removed.

Structured output is captured via in-process MCP tools: the agent invokes a
`submit_*` tool, the handler stuffs the validated args into a closure-shared
list, and the adapter returns them as a Pydantic model.

Tool restriction (load-bearing): `permission_mode="bypassPermissions"` auto-runs
every *available* tool without prompting, so availability — not the allow-list —
is what keeps an agent safe. We restrict availability with the SDK `tools`
parameter (CLI `--tools`): the single-tool paths (mermaid/grading) pass
`tools=[]` (no built-in tools at all), and the outline path passes
`tools=["Read","Grep","Glob"]` (read-only built-ins only — no Bash/Write/Edit, so
the agent cannot shell out or mutate the checkout it inspects). `allowed_tools`
only auto-approves; it does not gate availability.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
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
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from cognit.engine.llm import GenerateRequest
from cognit.engine.models import MermaidSet, MermaidSpec, QuizOutline
from cognit.ghio.diff import fetch_pr_diff

_TOOL_OUTLINE = "submit_quiz_outline"
_TOOL_MERMAID = "submit_mermaid_set"
_TOOL_GRADE = "submit_grade"
_TOOL_PR_DIFF = "pr_diff"

# Read-only built-in tools the outline agent may use to inspect the working tree.
# These are passed via `tools=` (availability), NOT just `allowed_tools=`.
_OUTLINE_BUILTIN_TOOLS = ["Read", "Grep", "Glob"]
# The exploration loop (pr_diff → several Read/Grep + thinking → submit) needs more
# than the near-single-shot budget the mermaid/grading paths use.
_OUTLINE_MAX_TURNS = 30
_INVOKE_MAX_TURNS = 8

_ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _load_prompt(name: str) -> str:
    return resources.files("cognit.engine.prompts").joinpath(name).read_text()


def _format_misconceptions(misconceptions: list[str]) -> str:
    return "\n".join(f"- {m}" for m in misconceptions)


def _repo_root() -> str:
    """Repo root of the current checkout — the cwd the outline agent reads from.

    `cognit take` runs from within the PR checkout, so the diff's repo-root-relative
    paths resolve against this. Falls back to the process cwd if not in a git repo.
    """
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.getcwd()


class ClaudeAgentLLM:
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model = model
        # Optional activity sink. When set (by `cognit take` during streamed
        # generation/grading), `_drain_agent` forwards Claude's text and tool
        # calls here instead of discarding them. Kept off the LLMClient Protocol —
        # only this adapter emits activity; other implementers (the test FakeLLM)
        # just never set it.
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
    ) -> None:
        """Build options and drive the SDK, mapping every failure to RuntimeError.

        `tools` is the availability restriction (CLI `--tools`); `allowed_tools` only
        auto-approves. The RuntimeError mapping is load-bearing — take.py and the tests
        rely on a single error type from this adapter.
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
            # "Reached maximum number of turns" — wrap to keep take.py's catch uniform.
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

        Keeps the `asyncio.run` body: the outline path runs only from the sync CLI
        (never under uvicorn), and grading is offloaded to a worker thread in
        server/app.py — so the loop-in-loop guard holds.
        """
        del handler  # production-side: handler is fired by the SDK, not by us

        async def _drain() -> None:
            async for msg in query(prompt=prompt, options=options):
                self._forward_activity(msg)

        asyncio.run(_drain())

    def _forward_activity(self, msg: Any) -> None:
        """Forward an assistant message's text + tool calls to `self.on_event`.

        No-op unless a sink is attached. Thinking blocks, tool results, and
        non-assistant messages (results/system) are intentionally ignored — the
        feed shows what Claude says and which tools it runs, not its reasoning.
        """
        if self.on_event is None or not isinstance(msg, AssistantMessage):
            return
        for block in msg.content:
            if isinstance(block, TextBlock):
                self.on_event({"kind": "text", "text": block.text, "tool": self._current_tool})
            elif isinstance(block, ToolUseBlock):
                self.on_event({"kind": "tool_use", "name": block.name, "tool": self._current_tool})

    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        """Stage 1 (agentic): the agent fetches the PR diff and reads the working tree
        with read-only tools, pulling only what it needs, then submits the outline."""
        system = _load_prompt("system_generate.txt")
        user = _load_prompt("generate.txt").format(
            pr_number=req.pr_number,
            branch=req.branch,
            pr_title=req.pr_title,
            pr_body=req.pr_body,
        )
        captured: list[dict[str, Any]] = []

        # Announce the phase so the streamed feed labels it (mirrors `_invoke_tool`;
        # this path drives the SDK directly so it must tag activity itself).
        self._current_tool = _TOOL_OUTLINE
        if self.on_event is not None:
            self.on_event({"kind": "step", "tool": _TOOL_OUTLINE})

        async def pr_diff_handler(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": fetch_pr_diff(req.pr_url)}]}

        async def submit_handler(args: dict[str, Any]) -> dict[str, Any]:
            captured.append(args)
            return {"content": [{"type": "text", "text": "ok"}]}

        pr_diff_tool = tool(
            _TOOL_PR_DIFF,
            "Fetch the PR's unified diff. Vendored/minified/lock/binary files are "
            "already stripped. Call this first to see what changed.",
            {"type": "object", "properties": {}},
        )(pr_diff_handler)
        submit_tool = tool(
            _TOOL_OUTLINE,
            "Submit the generated quiz outline.",
            QuizOutline.model_json_schema(),
        )(submit_handler)
        server = create_sdk_mcp_server(name="cognit", tools=[pr_diff_tool, submit_tool])

        self._run_agent(
            system=system,
            user=user,
            server=server,
            allowed_tools=[
                *_OUTLINE_BUILTIN_TOOLS,
                f"mcp__cognit__{_TOOL_PR_DIFF}",
                f"mcp__cognit__{_TOOL_OUTLINE}",
            ],
            tools=_OUTLINE_BUILTIN_TOOLS,
            max_turns=_OUTLINE_MAX_TURNS,
            cwd=_repo_root(),
            handler=submit_handler,
        )
        if not captured:
            raise RuntimeError(f"agent did not call {_TOOL_OUTLINE}")
        return QuizOutline.model_validate(captured[0])

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
