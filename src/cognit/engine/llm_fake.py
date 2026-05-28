class FakeLLM:
    """Test double for LLMClient. Returns canned outputs (or simple defaults)."""

    def __init__(
        self,
        canned_open_score: int = 100,
        canned_open_feedback: str = "",
    ):
        self._score = canned_open_score
        self._fb = canned_open_feedback

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        return self._score, self._fb
