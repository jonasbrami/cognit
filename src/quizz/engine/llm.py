from typing import Protocol

from pydantic import BaseModel

from quizz.engine.models import Quiz


class GenerateRequest(BaseModel):
    diff: str
    pr_title: str
    pr_body: str
    files: dict[str, str]  # path -> full content
    model: str = "gpt-4o-mini"


class LLMClient(Protocol):
    def generate_quiz(self, req: GenerateRequest) -> Quiz: ...

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]: ...
