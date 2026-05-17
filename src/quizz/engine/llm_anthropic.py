"""Anthropic SDK adapter for quizz. Uses tool use to enforce schema compliance."""

import json
import os
import time
from importlib import resources
from pathlib import Path
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import ToolParam, ToolUseBlock

from quizz.engine.llm import GenerateRequest
from quizz.engine.models import Quiz


# Beta header required when authenticating with a Claude Code OAuth token.
_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_CLAUDE_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"


def _load_claude_code_oauth() -> str | None:
    """Read the Claude Code OAuth access token from ~/.claude/.credentials.json.

    Returns the access token if present and unexpired, else None.
    """
    if not _CLAUDE_CREDS_PATH.exists():
        return None
    try:
        creds = json.loads(_CLAUDE_CREDS_PATH.read_text())
        oauth = creds.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        expires_at_ms = oauth.get("expiresAt")
        if not token:
            return None
        if expires_at_ms is not None and expires_at_ms < time.time() * 1000:
            # Expired — caller should re-run `claude login`.
            return None
        return str(token)
    except (json.JSONDecodeError, OSError):
        return None


def _no_anthropic_credentials() -> str:
    raise RuntimeError(
        "Anthropic provider needs credentials. Either:\n"
        "  - set ANTHROPIC_API_KEY (API key), or\n"
        "  - run `claude login` (uses your Claude Code OAuth session)"
    )


def _load_prompt(name: str) -> str:
    return resources.files("quizz.engine.prompts").joinpath(name).read_text()


_QUIZ_TOOL_NAME = "submit_quiz"
_GRADE_TOOL_NAME = "submit_grade"


class AnthropicLLM:
    """LLM client using Anthropic's tool use for guaranteed-schema output.

    Auth resolution order:
      1. Explicit `api_key` argument.
      2. `ANTHROPIC_API_KEY` env var.
      3. Claude Code OAuth session at `~/.claude/.credentials.json`
         (billed to the user's Claude Code subscription).

    Note: Anthropic doesn't have a `models/inference` endpoint with strict schema mode like
    OpenAI's `parse`. Instead we define a tool whose `input_schema` is the Quiz schema, and
    Claude is forced to call the tool with valid arguments. This gives us the same guarantee.
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

    def generate_quiz(self, req: GenerateRequest) -> Quiz:
        files_blob = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in req.files.items()
        )
        prompt_template = _load_prompt("generate.txt")
        # The schema is embedded in the tool; we omit it from the message to avoid duplication.
        # But we still need to fill the other placeholders.
        user_message = prompt_template.format(
            schema="(see the submit_quiz tool's input schema)",
            pr_title=req.pr_title,
            pr_body=req.pr_body,
            diff=req.diff,
            files=files_blob,
            question_mix=req.question_mix,
        )

        # Anthropic tool: input_schema = Quiz's JSON schema. Claude must call this tool.
        quiz_tool: ToolParam = {
            "name": _QUIZ_TOOL_NAME,
            "description": "Submit the generated quiz.",
            "input_schema": Quiz.model_json_schema(),
        }

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            tools=[quiz_tool],
            tool_choice={"type": "tool", "name": _QUIZ_TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract the tool_use block from the response
        for block in resp.content:
            if isinstance(block, ToolUseBlock) and block.name == _QUIZ_TOOL_NAME:
                data = cast(dict[str, Any], block.input)
                return Quiz.model_validate(data)

        raise RuntimeError("Anthropic did not return a tool_use block; cannot extract quiz")

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        prompt = _load_prompt("grade_open.txt").format(
            prompt=question_prompt,
            rubric=rubric,
            answer=answer,
        )
        grade_tool: ToolParam = {
            "name": _GRADE_TOOL_NAME,
            "description": "Submit a score and feedback for the open-ended answer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "feedback": {"type": "string"},
                },
                "required": ["score", "feedback"],
            },
        }
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            tools=[grade_tool],
            tool_choice={"type": "tool", "name": _GRADE_TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if isinstance(block, ToolUseBlock) and block.name == _GRADE_TOOL_NAME:
                data = cast(dict[str, Any], block.input)
                score = max(0, min(100, int(data.get("score", 0))))
                return score, str(data.get("feedback", ""))
        raise RuntimeError("Anthropic did not return a tool_use block; cannot extract grade")
