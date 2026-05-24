from collections.abc import Callable

from cognit.engine.llm import GenerateRequest
from cognit.engine.models import (
    MCQQuestion,
    MermaidSet,
    MermaidSpec,
    QuizDraft,
)


class FakeLLM:
    """Test double for LLMClient. Returns canned outputs (or simple defaults)."""

    def __init__(
        self,
        canned_draft: QuizDraft | None = None,
        canned_mermaid: MermaidSet | Callable[[MermaidSpec], MermaidSet] | None = None,
        canned_open_score: int = 100,
        canned_open_feedback: str = "",
    ):
        self._draft = canned_draft
        self._mermaid = canned_mermaid
        self._score = canned_open_score
        self._fb = canned_open_feedback

    def draft_quiz(self, req: GenerateRequest) -> QuizDraft:
        if self._draft is not None:
            return self._draft
        return QuizDraft(
            questions=[
                MCQQuestion(id="q1", prompt="default", options=["A", "B"], answer="A"),
            ]
        )

    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        if callable(self._mermaid):
            return self._mermaid(spec)
        if self._mermaid is not None:
            return self._mermaid
        return MermaidSet(
            options={
                "A": "flowchart LR\nA-->B",
                "B": "flowchart LR\nB-->A",
                "C": "flowchart LR\nA-->C",
                "D": "flowchart LR\nC-->A",
            },
            correct="A",
        )

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        return self._score, self._fb
