"""Anthropic SDK adapter for quizz. Uses tool use to enforce schema compliance."""

import os
from importlib import resources
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import ToolParam, ToolUseBlock

from quizz.engine.llm import GenerateRequest
from quizz.engine.models import Quiz


def _no_anthropic_key() -> str:
    raise RuntimeError("Anthropic provider requires ANTHROPIC_API_KEY to be set.")


def _load_prompt(name: str) -> str:
    return resources.files("quizz.engine.prompts").joinpath(name).read_text()


_QUIZ_TOOL_NAME = "submit_quiz"
_GRADE_TOOL_NAME = "submit_grade"


class AnthropicLLM:
    """LLM client using Anthropic's tool use for guaranteed-schema output.

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
        self._client = Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY") or _no_anthropic_key(),
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
