from typing import Protocol


class LLMClient(Protocol):
    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]: ...
