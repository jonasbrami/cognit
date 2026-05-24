from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, model_validator


class MCQQuestion(BaseModel):
    type: Literal["mcq"] = "mcq"
    id: str
    prompt: str
    options: list[str]
    answer: str  # must equal one of options

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


# --- Internal-only types used between stage 1 (outline) and stage 2 (mermaid artisan). ---
# Never serialized to PR comments. The final Quiz still uses `MermaidQuestion`.


class MermaidSpec(BaseModel):
    diagram_type: Literal["flowchart", "sequenceDiagram", "classDiagram", "stateDiagram"]
    correct_description: str  # natural-language description of the correct diagram
    misconceptions: list[str] = Field(min_length=3, max_length=3)  # three plausible distractors
    style_notes: str  # e.g., "5-6 nodes, LR direction, function-name labels"


class MermaidPlaceholder(BaseModel):
    """A mermaid question whose diagrams have not yet been rendered by the artisan subagent.

    Uses `type: "mermaid"` so the discriminator name matches what the system prompt
    instructs the LLM to emit. This is safe because `MermaidPlaceholder` and
    `MermaidQuestion` live in disjoint unions (`OutlineQuestion` vs. `Question`),
    so there's no collision in either discriminator.
    """

    type: Literal["mermaid"] = "mermaid"
    id: str
    prompt: str
    spec: MermaidSpec


OutlineQuestion = Annotated[
    Union[MCQQuestion, MermaidPlaceholder, OpenQuestion, TrueFalseQuestion],
    Field(discriminator="type"),
]


class QuizOutline(BaseModel):
    """Stage-1 quiz: mermaid questions are placeholders with specs; non-mermaid pass through."""

    version: Literal["1"] = "1"
    questions: list[OutlineQuestion]


class MermaidSet(BaseModel):
    """Stage-2 artisan output: 4 mermaid diagrams keyed A/B/C/D plus which is correct."""

    options: dict[str, str]  # exactly 4 entries, keys are A/B/C/D
    correct: str  # one of "A", "B", "C", "D"

    @model_validator(mode="after")
    def _shape_ok(self) -> "MermaidSet":
        if set(self.options) != {"A", "B", "C", "D"}:
            raise ValueError(f"options must be keyed A/B/C/D, got {sorted(self.options)!r}")
        if self.correct not in self.options:
            raise ValueError(f"correct={self.correct!r} not a key of options")
        return self


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
