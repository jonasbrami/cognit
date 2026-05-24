from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, model_validator


class MCQQuestion(BaseModel):
    type: Literal["mcq"] = "mcq"
    id: str
    prompt: str
    options: list[str]
    answer: str  # must equal one of options
    explanation: str = ""  # shown to the reader after they answer (the "aha")

    @model_validator(mode="after")
    def _answer_in_options(self) -> "MCQQuestion":
        if self.answer not in self.options:
            raise ValueError(f"answer {self.answer!r} not in options {self.options!r}")
        return self


class MermaidQuestion(BaseModel):
    type: Literal["mermaid"] = "mermaid"
    id: str
    prompt: str
    options: dict[str, str]  # label -> mermaid source
    answer: str  # must be a key of options
    explanation: str = ""  # shown to the reader after they answer (the "aha")

    @model_validator(mode="after")
    def _answer_is_option_key(self) -> "MermaidQuestion":
        if self.answer not in self.options:
            raise ValueError(f"answer {self.answer!r} not in options {list(self.options)!r}")
        return self


class OpenQuestion(BaseModel):
    type: Literal["open"] = "open"
    id: str
    prompt: str
    rubric: str


class TrueFalseQuestion(BaseModel):
    type: Literal["tf"] = "tf"
    id: str
    prompt: str
    answer: bool
    explanation: str = ""  # shown to the reader after they answer (the "aha")


Question = Annotated[
    Union[MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion],
    Field(discriminator="type"),
]


class Quiz(BaseModel):
    version: Literal["1"] = "1"
    pr_number: int
    questions: list[Question]


class QuizDraft(BaseModel):
    """What the single generation agent submits: the final question shapes, no
    pr_number (the orchestrator supplies it). Mermaid questions are fully rendered."""

    version: Literal["1"] = "1"
    questions: list[Question]


# --- Answer / Results models (unchanged) ---


class AnswerEntry(BaseModel):
    question_id: str
    value: str  # for MCQ/mermaid: option label; for open: free text; for tf: "true"/"false"


class Answers(BaseModel):
    version: Literal["1"] = "1"
    pr_number: int
    entries: list[AnswerEntry]


class QuestionResult(BaseModel):
    question_id: str
    correct: bool
    score: int = Field(ge=0, le=100)  # 0..100
    feedback: str  # "" for deterministic questions


class Results(BaseModel):
    version: Literal["1"] = "1"
    pr_number: int
    total_score: int = Field(ge=0, le=100)
    per_question: list[QuestionResult]
