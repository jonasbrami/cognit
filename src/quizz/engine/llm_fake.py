from quizz.engine.llm import GenerateRequest
from quizz.engine.models import Quiz, MCQQuestion


class FakeLLM:
    def __init__(
        self,
        canned_quiz: Quiz | None = None,
        canned_open_score: int = 100,
        canned_open_feedback: str = "",
    ):
        self._quiz = canned_quiz
        self._score = canned_open_score
        self._fb = canned_open_feedback

    def generate_quiz(self, req: GenerateRequest) -> Quiz:
        if self._quiz is not None:
            return self._quiz
        return Quiz(
            pr_number=0,
            questions=[MCQQuestion(id="q1", prompt="default", options=["A", "B"], answer="A")],
        )

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        return self._score, self._fb
