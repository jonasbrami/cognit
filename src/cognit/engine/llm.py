from typing import Protocol

from pydantic import BaseModel

from cognit.engine.models import MermaidSet, MermaidSpec, QuizOutline


class GenerateRequest(BaseModel):
    pr_title: str
    pr_body: str
    pr_number: int
    pr_url: str  # used by the outline agent's `pr_diff` tool to fetch the diff itself
    branch: str  # PR head branch (already checked out); passed to the agent as context
    model: str = "claude-sonnet-4-6"


class LLMClient(Protocol):
    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        """Stage 1: produce a quiz outline. Mermaid questions are returned as placeholders
        carrying a structured spec; the engine then dispatches `generate_mermaid_set` per
        placeholder to render the diagrams."""

    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        """Stage 2: render 4 mermaid diagrams (1 correct + 3 plausible distractors) in
        uniform style, given a structured spec produced in stage 1."""

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]: ...
