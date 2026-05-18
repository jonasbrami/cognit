"""Anthropic SDK adapter for quizz.

Uses tool use to enforce schema compliance on outputs. The generation pipeline is split
into two stages:

  1. `generate_quiz_outline` — author picks questions and emits a structured spec for
     each mermaid question (no diagram syntax yet).
  2. `generate_mermaid_set` — a focused artisan subagent renders 4 uniform diagrams per
     spec. The engine fans these out in parallel.

Each call uses a `system=` parameter with `cache_control: ephemeral` on the static
instruction text, so retries and per-question subagent calls within one run share a
cached prefix.
"""

import json
import os
import time
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn, cast

from anthropic import Anthropic
from anthropic.types import CacheControlEphemeralParam, TextBlockParam, ToolParam, ToolUseBlock

from quizz.engine.llm import GenerateRequest
from quizz.engine.models import MermaidSet, MermaidSpec, QuizOutline


# Beta header required when authenticating with a Claude Code OAuth token.
_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_CLAUDE_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"

_TOOL_OUTLINE = "submit_quiz_outline"
_TOOL_MERMAID = "submit_mermaid_set"
_TOOL_GRADE = "submit_grade"


def _load_claude_code_oauth() -> str | None:
    """Read the Claude Code OAuth access token from ~/.claude/.credentials.json.

    Returns the token if valid, None if the credentials file is missing or unreadable.
    Raises RuntimeError with a specific message if the file exists but the token is
    expired — that's an actionable user problem (run `claude login`), not a "no creds"
    condition, and the user deserves to be told the actual cause.
    """
    if not _CLAUDE_CREDS_PATH.exists():
        return None
    try:
        creds = json.loads(_CLAUDE_CREDS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    oauth = creds.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    expires_at_ms = oauth.get("expiresAt")
    if not token:
        return None
    if expires_at_ms is not None and expires_at_ms < time.time() * 1000:
        raise RuntimeError(
            "Your Claude Code OAuth session is expired. Run `claude login` to refresh, "
            "or set ANTHROPIC_API_KEY to use an API key instead."
        )
    return str(token)


def _no_anthropic_credentials() -> NoReturn:
    raise RuntimeError(
        "Anthropic provider needs credentials. Either:\n"
        "  - set ANTHROPIC_API_KEY (API key), or\n"
        "  - run `claude login` (uses your Claude Code OAuth session)"
    )


def _load_prompt(name: str) -> str:
    return resources.files("quizz.engine.prompts").joinpath(name).read_text()


def _system_block(text: str) -> TextBlockParam:
    """Build a system content block with ephemeral prompt-caching enabled.

    Sent as a list-of-blocks (not a string) so cache_control attaches to the static
    instruction text. Cache hits last ~5 minutes, which covers all subagent calls in
    one `quizz generate` run.
    """
    return TextBlockParam(
        type="text",
        text=text,
        cache_control=CacheControlEphemeralParam(type="ephemeral"),
    )


def _format_files_blob(files: dict[str, str]) -> str:
    if not files:
        return ""
    return "\n".join(f'<file path="{path}">\n{content}\n</file>' for path, content in files.items())


def _format_misconceptions(misconceptions: list[str]) -> str:
    return "\n".join(f"- {m}" for m in misconceptions)


def _extract_tool_input(resp: Any, tool_name: str) -> dict[str, Any]:
    """Pull the tool_use block matching `tool_name` out of an Anthropic response."""
    for block in resp.content:
        if isinstance(block, ToolUseBlock) and block.name == tool_name:
            return cast(dict[str, Any], block.input)
    raise RuntimeError(f"Anthropic did not return a tool_use block for {tool_name!r}")


class AnthropicLLM:
    """LLM client using Anthropic's tool use for guaranteed-schema output.

    Auth resolution order:
      1. Explicit `api_key` argument.
      2. `ANTHROPIC_API_KEY` env var.
      3. Claude Code OAuth session at `~/.claude/.credentials.json`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 8192,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens

        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if resolved_api_key:
            self._client = Anthropic(api_key=resolved_api_key)
        else:
            oauth_token = _load_claude_code_oauth()
            if oauth_token is None:
                _no_anthropic_credentials()
            self._client = Anthropic(
                auth_token=oauth_token,
                default_headers={"anthropic-beta": _OAUTH_BETA_HEADER},
            )

    # --- Stage 1: outline ---

    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        system = _load_prompt("system_generate.txt")
        user_message = _load_prompt("generate.txt").format(
            pr_title=req.pr_title,
            pr_body=req.pr_body,
            diff=req.diff,
            files=_format_files_blob(req.files),
        )
        tool: ToolParam = {
            "name": _TOOL_OUTLINE,
            "description": "Submit the generated quiz outline.",
            "input_schema": QuizOutline.model_json_schema(),
        }
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[_system_block(system)],
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_OUTLINE},
            messages=[{"role": "user", "content": user_message}],
        )
        data = _extract_tool_input(resp, _TOOL_OUTLINE)
        return QuizOutline.model_validate(data)

    # --- Stage 2: mermaid artisan ---

    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        system = _load_prompt("system_mermaid.txt")
        # The artisan does not need the full diff/files — the outline LLM has already
        # digested the change into a spec. Keeping the user message focused keeps the
        # subagent on-task and the per-call token cost low.
        user_message = _load_prompt("mermaid.txt").format(
            diagram_type=spec.diagram_type,
            correct_description=spec.correct_description,
            misconceptions=_format_misconceptions(spec.misconceptions),
            style_notes=spec.style_notes,
        )
        tool: ToolParam = {
            "name": _TOOL_MERMAID,
            "description": "Submit 4 mermaid diagrams keyed A/B/C/D plus which is correct.",
            "input_schema": {
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
            },
        }
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=[_system_block(system)],
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_MERMAID},
            messages=[{"role": "user", "content": user_message}],
        )
        data = _extract_tool_input(resp, _TOOL_MERMAID)
        return MermaidSet.model_validate(data)

    # --- Grading ---

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        system = _load_prompt("system_grade.txt")
        user_message = _load_prompt("grade_open.txt").format(
            prompt=question_prompt,
            rubric=rubric,
            answer=answer,
        )
        tool: ToolParam = {
            "name": _TOOL_GRADE,
            "description": "Submit a score and feedback for the open-ended answer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "feedback": {"type": "string"},
                },
                "required": ["score", "feedback"],
                "additionalProperties": False,
            },
        }
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=[_system_block(system)],
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_GRADE},
            messages=[{"role": "user", "content": user_message}],
        )
        data = _extract_tool_input(resp, _TOOL_GRADE)
        score = max(0, min(100, int(data.get("score", 0))))
        return score, str(data.get("feedback", ""))
