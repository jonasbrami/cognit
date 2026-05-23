# PR Author Quiz v1 — Implementation Plan

> **STATUS (as shipped):** ✅ Delivered, but with deviations from the original plan. See the "What actually shipped" section below before reading the per-task code listings — they're now historical and don't match the live source in several places. Current truth lives in `INTENTS.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## What actually shipped (vs the plan)

Major deviations from the original plan:

- **GitHub Actions removed.** Both M4 (generator Action) and M6.2 (grader Action) were prototyped end-to-end against a private sandbox repo, then deliberately removed before shipping. Reasons: GitHub Models rejected the Pydantic strict schema; the fallback path got malformed JSON from `gpt-4o-mini`. We pivoted to local CLI as the canonical path. The Action auto-trigger remains in the v2 backlog. The `cognit generate` and `cognit grade` CLI commands (originally documented as "internal — used by the Action") are now the user-facing surface.
- **Anthropic LLM adapter added as the default** (not in the original plan, which spec'd GitHub Models only). Uses tool use for guaranteed-schema output. Auth resolution: `api_key` arg → `ANTHROPIC_API_KEY` env → Claude Code OAuth at `~/.claude/.credentials.json`. The GitHub Models adapter is kept as `--llm github` but is not the recommended path.
- **Question count is now LLM-decided** (originally fixed at 5: "2 MCQ + 1 mermaid + 1 open + 1 tf"). The prompt now tells the model to pick the count and type-mix based on diff size and complexity (typical range 2–10). `question_mix` was removed from `GenerateRequest`.
- **`cognit take` flow changed.** Originally: `/submit` posts answers comment + browser polls `/results` waiting for the grader Action. Shipped: `/submit` grades EVERYTHING in-session (deterministic + LLM open-question), returns full results inline, **nothing posts to the PR**. A "Publish results to PR" button (POST `/publish`) gives the user opt-in control. The `/results` polling endpoint is gone.
- **UI is editorially redesigned** (warm paper + ink palette, Fraunces serif headline with rust italic accent, blueprint-styled mermaid options, margin-rail ordinals i/ii/iii). The plan called for "vanilla HTML/JS/CSS" — that's still true, but the visual language is much more distinctive than the original placeholder styles.
- **Mermaid bundle is the UMD build** (`mermaid.min.js`, 3.2MB, single self-contained file) loaded via `<script>`, not the ESM bundle the plan suggested. Reason: jsdelivr's `+esm` mermaid had unresolvable nested imports (`/npm/d3@.../+esm`).
- **Mermaid option labels are auto-neutralized to A/B/C/D** by a post-processor in `engine/generate.py`, regardless of whatever the LLM produced (it was emitting semantic labels like `correct`/`wrong_1` which leaked the answer).
- **Anthropic adapter coerces `pr_number` to 0 before validation.** The model sometimes fills the schema-required `pr_number` field with placeholder strings (`<UNKNOWN>`); the engine overwrites with the real value immediately, so the adapter just sets it to 0 to satisfy Pydantic.

What stayed faithful to the plan:
- Python 3.12+ + uv + typer + pydantic v2 + FastAPI/uvicorn + httpx/respx + pytest + ruff + mypy.
- Five-package layered architecture: `engine` (pure) → `comment` (pure) → `ghio` / `server` / `cli`.
- TDD discipline through M1–M2 (Pydantic models, comment serialization).
- `gh` CLI for all GitHub I/O.
- `mmdc` validator (optional, skipped silently when missing).
- MIT license, GoReleaser-equivalent flow via `release.yml` (still untagged at the time of writing).

The code listings in the milestone sections below are historical — they were faithful to the plan as authored, but the shipped code differs in the places above. Use them as guidance for the design intent, not as a reference for current source. The actual source lives at `src/cognit/` and the design contract lives in `INTENTS.md`.

---

**Goal (original):** Ship a voluntary, opt-in PR-author quiz tool: a portable Python engine, two GitHub Composite Actions (generator + grader), and a single CLI (`cognit take`). End-to-end flow: PR opens → Action posts quiz comment → author runs `cognit take` → local browser quiz → answers comment posted → grader Action LLM-grades open question → results comment posted.

**Goal (as shipped):** Local CLI only. Three subcommands (`generate`, `take`, `grade`). Flow: author opens PR, runs `cognit generate --post`, runs `cognit take` (browser opens, in-session grading via LLM, opt-in Publish button), done. The Actions wrapper is v2 work.

**Architecture:** Five-layer package design with a deliberate engine boundary. The `engine` and `comment` packages are pure (no I/O, no GitHub knowledge). `ghio` is the only place that shells to `gh`. `server` is the local FastAPI app for `cognit take`. `cli` is the thin glue. Same engine reused by `generate`, `take`, and `grade` subcommands — and by the v2 GitHub App later.

**Tech stack:** Python 3.12+, `uv` (env/build/publish), `typer` (CLI), `pydantic` v2 (models everywhere), `openai` SDK (against GitHub Models endpoint), `FastAPI`+`uvicorn` (local server), `httpx`+`respx` (HTTP + test mocking), `pytest`+`syrupy` (tests + snapshots), `ruff`+`mypy` (lint/typecheck), `gh` CLI (GitHub I/O), `@mermaid-js/mermaid-cli` (mermaid validation).

---

## File layout

```
cognit/
├── pyproject.toml
├── README.md
├── LICENSE                        # MIT
├── CHANGELOG.md
├── uv.lock
│
├── src/cognit/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli/
│   │   ├── __init__.py            # typer app + subcommand registration
│   │   ├── take.py
│   │   ├── generate.py
│   │   ├── grade.py
│   │   └── version.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── models.py              # Pydantic schemas
│   │   ├── llm.py                 # Protocol + factory
│   │   ├── llm_githubmodels.py
│   │   ├── llm_fake.py            # test double
│   │   ├── generate.py
│   │   ├── grade.py
│   │   ├── mermaid.py
│   │   └── prompts/
│   │       ├── generate.txt
│   │       └── grade_open.txt
│   ├── comment/
│   │   ├── __init__.py
│   │   ├── render.py
│   │   └── parse.py
│   ├── ghio/
│   │   ├── __init__.py
│   │   ├── pr.py
│   │   └── diff.py
│   └── server/
│       ├── __init__.py
│       ├── app.py
│       └── assets/
│           ├── index.html
│           ├── quiz.js
│           ├── styles.css
│           └── mermaid.esm.min.js
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── diffs/
│   │   │   ├── small_refactor.patch
│   │   │   ├── new_feature.patch
│   │   │   └── bug_fix.patch
│   │   └── quizzes/
│   ├── engine/
│   ├── comment/
│   ├── ghio/
│   ├── server/
│   └── cli/
│
├── actions/
│   ├── cognit-generate/action.yml
│   └── cognit-grade/action.yml
│
└── .github/
    ├── workflows/
    │   ├── ci.yml
    │   └── release.yml
    └── examples/
        ├── cognit-generate.yml
        └── cognit-grade.yml
```

---

## Architecture

Layered, with a strict engine boundary so the same logic feeds v1 (Action+CLI) and v2 (GitHub App).

### Dependency graph

```
                ┌──────────────────────────────────────┐
                │           cognit.cli                  │
                │   (typer entry points — glue)        │
                │                                      │
                │   generate.py    take.py    grade.py │
                └──────┬──────────────┬─────────┬──────┘
                       │              │         │
                       ▼              ▼         ▼
        ┌──────────┐   ┌─────────┐   ┌─────────┐
        │  ghio    │   │ server  │   │ comment │
        │ gh CLI   │   │ FastAPI │   │ md ↔ obj│
        └─────┬────┘   └────┬────┘   └────┬────┘
              │             │             │
              │      ┌──────┘             │
              │      │                    │
              ▼      ▼                    ▼
        ┌─────────────────────────────────────┐
        │              engine                 │
        │   models (pydantic) · llm (proto)   │
        │   generate · grade · mermaid        │
        │   prompts/                          │
        └─────────────────────────────────────┘
                            │
                            ▼
                    GitHub Models API
                    (mmdc subprocess)
```

### Layer responsibilities

| Package | Knows | Doesn't know |
|---|---|---|
| `engine` | Pydantic models, LLM Protocol, mermaid CLI, prompts | GitHub, PRs, comments, HTTP, files |
| `comment` | Engine models, markdown format | GitHub, HTTP, files |
| `ghio` | `gh` CLI, subprocess, JSON parsing | Quiz semantics, engine internals |
| `server` | Engine models, static assets, FastAPI | GitHub (cli passes ghio funcs in) |
| `cli` | Everything | — |

### Three entry points, same engine

```
cognit generate  =  ghio.fetch_pr() → engine.generate() → comment.render() → ghio.post()
cognit take      =  ghio.fetch_pr() → comment.parse()  → server.run() → ghio.post()
cognit grade     =  ghio.fetch_pr() → comment.parse()  → engine.grade() → comment.render() → ghio.post()
```

Each subcommand is 30–60 lines of glue. All real logic lives below.

### Single source of truth for the schema

`Quiz`, `Question` (discriminated union: MCQ/Mermaid/Open/TrueFalse), `Answers`, `Results` are defined once in `engine/models.py`. The same models flow through:
- LLM structured-output (`response_format=Quiz`)
- The JSON state block in PR comments
- The browser's quiz payload
- Internal engine/server/cli types

No serialization mismatches possible.

---

## Milestones and tasks

Each task: file paths, TDD steps, real code, commit. Linear chain — earlier tasks set up types used in later tasks. Solo dev expected ordering.

---

## M1 — Engine library

### Task M1.1: Project skeleton

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/cognit/__init__.py`, `src/cognit/__main__.py`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Initialize project**

```bash
uv init --package cognit
cd cognit
uv add pydantic typer "openai>=1.50" httpx
uv add --dev pytest pytest-asyncio respx syrupy ruff mypy
```

- [ ] **Step 2: Write `pyproject.toml` entry-point + tool config**

```toml
[project]
name = "cognit"
version = "0.1.0"
description = "Voluntary PR-author comprehension quiz tool"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "openai>=1.50",
    "httpx>=0.27",
]

[project.scripts]
cognit = "cognit.cli:app"

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "respx", "syrupy", "ruff", "mypy"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
strict = true
files = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Smoke test**

```python
# tests/test_smoke.py
import cognit

def test_package_imports():
    assert cognit.__name__ == "cognit"
```

```bash
uv run pytest tests/test_smoke.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git init
git add .
git commit -m "chore: project skeleton (uv, pytest, ruff, mypy)"
```

### Task M1.2: Question models (MCQ, Mermaid, Open, TrueFalse) + Quiz/Answers/Results

**Files:**
- Create: `src/cognit/engine/__init__.py`, `src/cognit/engine/models.py`, `tests/engine/__init__.py`, `tests/engine/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_models.py
import pytest
from pydantic import ValidationError
from cognit.engine.models import (
    Quiz, Question, MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion,
    Answers, AnswerEntry, Results, QuestionResult,
)

def test_mcq_question_round_trip():
    q = MCQQuestion(id="q1", prompt="Why?", options=["A", "B", "C"], answer="B")
    data = q.model_dump()
    assert MCQQuestion.model_validate(data) == q

def test_mcq_answer_must_be_one_of_options():
    with pytest.raises(ValidationError):
        MCQQuestion(id="q1", prompt="Why?", options=["A", "B"], answer="Z")

def test_mermaid_question():
    q = MermaidQuestion(
        id="q2", prompt="Which diagram?",
        options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"},
        answer="A",
    )
    assert q.answer == "A"

def test_open_question():
    q = OpenQuestion(id="q3", prompt="Explain.", rubric="Mentions X, Y.")
    assert q.rubric.startswith("Mentions")

def test_tf_question():
    q = TrueFalseQuestion(id="q4", prompt="Is it?", answer=True)
    assert q.answer is True

def test_quiz_discriminated_union():
    quiz = Quiz(
        version="1", pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A"),
            OpenQuestion(id="q3", prompt="?", rubric="r"),
        ],
    )
    raw = quiz.model_dump_json()
    parsed = Quiz.model_validate_json(raw)
    assert parsed == quiz
    assert isinstance(parsed.questions[0], MCQQuestion)
    assert isinstance(parsed.questions[1], OpenQuestion)

def test_answers_results():
    a = Answers(pr_number=42, entries=[AnswerEntry(question_id="q1", value="A")])
    r = Results(
        pr_number=42, total_score=80,
        per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")],
    )
    assert a.entries[0].value == "A"
    assert r.total_score == 80
```

```bash
uv run pytest tests/engine/test_models.py -v
```
Expected: FAIL (models don't exist).

- [ ] **Step 2: Implement models**

```python
# src/cognit/engine/models.py
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
    score: int  # 0..100
    feedback: str  # for open questions; "" for deterministic ones


class Results(BaseModel):
    version: Literal["1"] = "1"
    pr_number: int
    total_score: int
    per_question: list[QuestionResult]
```

- [ ] **Step 3: Re-run tests**

```bash
uv run pytest tests/engine/test_models.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/engine/ tests/engine/
git commit -m "feat(engine): pydantic models for Quiz/Question/Answers/Results"
```

### Task M1.3: LLM Protocol + fake adapter

**Files:**
- Create: `src/cognit/engine/llm.py`, `src/cognit/engine/llm_fake.py`, `tests/engine/test_llm_fake.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_llm_fake.py
from cognit.engine.llm import LLMClient, GenerateRequest
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import Quiz, MCQQuestion

def test_fake_returns_canned_quiz():
    canned = Quiz(
        pr_number=1,
        questions=[MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="A")],
    )
    llm: LLMClient = FakeLLM(canned_quiz=canned)
    out = llm.generate_quiz(GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}))
    assert out == canned

def test_fake_grades_open_question():
    llm = FakeLLM(canned_open_score=75, canned_open_feedback="ok")
    score, fb = llm.grade_open(
        question_prompt="why?", rubric="r", answer="because",
    )
    assert score == 75
    assert fb == "ok"
```

- [ ] **Step 2: Define Protocol + fake**

```python
# src/cognit/engine/llm.py
from typing import Protocol
from pydantic import BaseModel
from cognit.engine.models import Quiz


class GenerateRequest(BaseModel):
    diff: str
    pr_title: str
    pr_body: str
    files: dict[str, str]  # path -> full content
    question_mix: dict[str, int] = {"mcq": 2, "mermaid": 1, "open": 1, "tf": 1}
    model: str = "gpt-4o-mini"


class LLMClient(Protocol):
    def generate_quiz(self, req: GenerateRequest) -> Quiz: ...
    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]: ...
```

```python
# src/cognit/engine/llm_fake.py
from cognit.engine.llm import GenerateRequest
from cognit.engine.models import Quiz, MCQQuestion


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
            questions=[MCQQuestion(id="q1", prompt="default", options=["A","B"], answer="A")],
        )

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        return self._score, self._fb
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/engine/test_llm_fake.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/engine/llm.py src/cognit/engine/llm_fake.py tests/engine/test_llm_fake.py
git commit -m "feat(engine): LLMClient Protocol + FakeLLM test double"
```

### Task M1.4: GitHub Models adapter

**Files:**
- Create: `src/cognit/engine/llm_githubmodels.py`, `src/cognit/engine/prompts/generate.txt`, `src/cognit/engine/prompts/grade_open.txt`, `tests/engine/test_llm_githubmodels.py`

- [ ] **Step 1: Write failing test using `respx`**

```python
# tests/engine/test_llm_githubmodels.py
import json
import respx
import httpx
from cognit.engine.llm import GenerateRequest
from cognit.engine.llm_githubmodels import GitHubModelsLLM
from cognit.engine.models import Quiz, MCQQuestion

@respx.mock
def test_generate_quiz_hits_models_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    canned = Quiz(
        pr_number=42,
        questions=[MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="A")],
    )
    route = respx.post("https://models.github.ai/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": canned.model_dump_json()}}]},
        )
    )
    llm = GitHubModelsLLM()
    out = llm.generate_quiz(GenerateRequest(
        diff="x", pr_title="t", pr_body="b", files={},
    ))
    assert route.called
    assert out == canned
```

- [ ] **Step 2: Implement adapter**

```python
# src/cognit/engine/prompts/generate.txt
You are a comprehension quiz author. Given a unified diff and the full content of touched files, generate exactly the requested mix of questions probing whether the PR author understands what their code does.

Return JSON matching this schema strictly: {schema}

Mermaid questions: produce ONE correct diagram representing the change, plus 3 plausible-but-wrong mutations of it (swap an edge, drop a node, add a wrong fork). All 4 must use identical layout style, identical node-naming style, identical edge density. Each diagram must be valid mermaid syntax.

PR title: {pr_title}
PR body: {pr_body}
Diff:
{diff}

Touched files (full content):
{files}

Question mix: {question_mix}
```

```python
# src/cognit/engine/prompts/grade_open.txt
You are grading a developer's answer to a comprehension question about their own PR.

Question: {prompt}
Rubric: {rubric}
Developer's answer: {answer}

Return JSON: {{"score": <0..100>, "feedback": "<concise explanation>"}}.
Score 90+ if the answer demonstrates accurate understanding matching the rubric.
Score 50-89 for partial understanding.
Score below 50 for misunderstanding.
```

```python
# src/cognit/engine/llm_githubmodels.py
import json
import os
from importlib import resources
from openai import OpenAI
from cognit.engine.llm import GenerateRequest
from cognit.engine.models import Quiz


def _load_prompt(name: str) -> str:
    return resources.files("cognit.engine.prompts").joinpath(name).read_text()


class GitHubModelsLLM:
    def __init__(self, base_url: str = "https://models.github.ai/inference", token: str | None = None):
        self._client = OpenAI(
            base_url=base_url,
            api_key=token or os.environ["GITHUB_TOKEN"],
        )

    def generate_quiz(self, req: GenerateRequest) -> Quiz:
        files_blob = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in req.files.items()
        )
        prompt = _load_prompt("generate.txt").format(
            schema=Quiz.model_json_schema(),
            pr_title=req.pr_title,
            pr_body=req.pr_body,
            diff=req.diff,
            files=files_blob,
            question_mix=req.question_mix,
        )
        resp = self._client.chat.completions.create(
            model=req.model,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content or "{}"
        return Quiz.model_validate_json(content)

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        prompt = _load_prompt("grade_open.txt").format(
            prompt=question_prompt, rubric=rubric, answer=answer,
        )
        resp = self._client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return int(data.get("score", 0)), str(data.get("feedback", ""))
```

- [ ] **Step 3: Run test**

```bash
uv run pytest tests/engine/test_llm_githubmodels.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/engine/llm_githubmodels.py src/cognit/engine/prompts/ tests/engine/test_llm_githubmodels.py
git commit -m "feat(engine): GitHub Models adapter + prompt templates"
```

### Task M1.5: Mermaid validator

**Files:**
- Create: `src/cognit/engine/mermaid.py`, `tests/engine/test_mermaid.py`

- [ ] **Step 1: Write failing test**

```python
# tests/engine/test_mermaid.py
from cognit.engine.mermaid import is_valid_mermaid, MermaidUnavailable
import pytest, shutil

@pytest.mark.skipif(not shutil.which("mmdc"), reason="mmdc not installed")
def test_valid_diagram():
    assert is_valid_mermaid("flowchart LR\nA --> B")

@pytest.mark.skipif(not shutil.which("mmdc"), reason="mmdc not installed")
def test_invalid_diagram():
    assert not is_valid_mermaid("not actually mermaid {{{")

def test_raises_if_mmdc_missing(monkeypatch):
    monkeypatch.setattr("cognit.engine.mermaid._which_mmdc", lambda: None)
    with pytest.raises(MermaidUnavailable):
        is_valid_mermaid("flowchart LR\nA --> B", strict=True)

def test_skip_if_missing_returns_true_by_default(monkeypatch):
    monkeypatch.setattr("cognit.engine.mermaid._which_mmdc", lambda: None)
    assert is_valid_mermaid("anything", strict=False) is True
```

- [ ] **Step 2: Implement validator**

```python
# src/cognit/engine/mermaid.py
import shutil
import subprocess
import tempfile
from pathlib import Path


class MermaidUnavailable(RuntimeError):
    """Raised when strict=True and mmdc is not installed."""


def _which_mmdc() -> str | None:
    return shutil.which("mmdc")


def is_valid_mermaid(source: str, *, strict: bool = False) -> bool:
    """Parse-check a mermaid source. If mmdc is missing, skip (return True) unless strict."""
    mmdc = _which_mmdc()
    if mmdc is None:
        if strict:
            raise MermaidUnavailable("mmdc not on PATH; install @mermaid-js/mermaid-cli")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.mmd"
        out = Path(tmp) / "out.svg"
        inp.write_text(source)
        result = subprocess.run(
            [mmdc, "-i", str(inp), "-o", str(out)],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/engine/test_mermaid.py -v
```
Expected: PASS (tests that need mmdc skip if absent).

- [ ] **Step 4: Commit**

```bash
git add src/cognit/engine/mermaid.py tests/engine/test_mermaid.py
git commit -m "feat(engine): mermaid validator wrapping mmdc"
```

### Task M1.6: Generation pipeline with mermaid retry

**Files:**
- Create: `src/cognit/engine/generate.py`, `tests/engine/test_generate.py`, `tests/fixtures/diffs/small_refactor.patch`

- [ ] **Step 1: Add fixture**

```diff
# tests/fixtures/diffs/small_refactor.patch
--- a/cache.py
+++ b/cache.py
@@ -1,5 +1,8 @@
+import threading
+
 class Cache:
-    def __init__(self):
-        self.store = {}
+    def __init__(self, max_size: int = 1000):
+        self.store = {}
+        self.max_size = max_size
+        self.lock = threading.RLock()
```

- [ ] **Step 2: Write failing test**

```python
# tests/engine/test_generate.py
from pathlib import Path
from cognit.engine.generate import generate_quiz
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import Quiz, MCQQuestion, MermaidQuestion, OpenQuestion

FIX = Path(__file__).parent.parent / "fixtures"

def test_generate_returns_quiz_via_llm():
    diff = (FIX / "diffs" / "small_refactor.patch").read_text()
    canned = Quiz(
        pr_number=1,
        questions=[
            MCQQuestion(id="q1", prompt="why lock?", options=["safety","speed"], answer="safety"),
            MermaidQuestion(
                id="q2", prompt="which flow?",
                options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"}, answer="A",
            ),
            OpenQuestion(id="q3", prompt="rationale?", rubric="thread safety"),
        ],
    )
    out = generate_quiz(
        diff=diff, pr_title="add lock", pr_body="", files={"cache.py": "..."},
        pr_number=1, llm=FakeLLM(canned_quiz=canned),
    )
    assert out == canned

def test_generate_drops_invalid_mermaid(monkeypatch):
    """If mmdc rejects every mermaid candidate after retries, drop the mermaid Q."""
    monkeypatch.setattr("cognit.engine.generate._validate_mermaid", lambda src: False)
    canned = Quiz(
        pr_number=1,
        questions=[
            MermaidQuestion(
                id="q1", prompt="?",
                options={"A": "bad", "B": "bad"}, answer="A",
            ),
            MCQQuestion(id="q2", prompt="?", options=["x","y"], answer="x"),
        ],
    )
    out = generate_quiz(
        diff="x", pr_title="t", pr_body="", files={},
        pr_number=1, llm=FakeLLM(canned_quiz=canned), max_mermaid_retries=0,
    )
    assert not any(q.type == "mermaid" for q in out.questions)
```

- [ ] **Step 3: Implement**

```python
# src/cognit/engine/generate.py
from cognit.engine.llm import LLMClient, GenerateRequest
from cognit.engine.models import Quiz, MermaidQuestion, MCQQuestion
from cognit.engine.mermaid import is_valid_mermaid


def _validate_mermaid(source: str) -> bool:
    return is_valid_mermaid(source, strict=False)


def generate_quiz(
    *,
    diff: str,
    pr_title: str,
    pr_body: str,
    files: dict[str, str],
    pr_number: int,
    llm: LLMClient,
    max_mermaid_retries: int = 2,
) -> Quiz:
    req = GenerateRequest(
        diff=diff, pr_title=pr_title, pr_body=pr_body, files=files,
    )
    quiz = llm.generate_quiz(req)
    quiz = Quiz(version="1", pr_number=pr_number, questions=quiz.questions)

    for attempt in range(max_mermaid_retries + 1):
        bad = [q for q in quiz.questions
               if isinstance(q, MermaidQuestion)
               and not all(_validate_mermaid(src) for src in q.options.values())]
        if not bad:
            return quiz
        if attempt < max_mermaid_retries:
            retried = llm.generate_quiz(req)
            quiz = Quiz(version="1", pr_number=pr_number, questions=retried.questions)

    # Last resort: drop mermaid questions
    kept = [q for q in quiz.questions if not isinstance(q, MermaidQuestion)]
    return Quiz(version="1", pr_number=pr_number, questions=kept)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/engine/test_generate.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cognit/engine/generate.py tests/engine/test_generate.py tests/fixtures/
git commit -m "feat(engine): generation pipeline with mermaid retry"
```

### Task M1.7: Grading (deterministic + LLM for open)

**Files:**
- Create: `src/cognit/engine/grade.py`, `tests/engine/test_grade.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_grade.py
from cognit.engine.grade import grade
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import (
    Quiz, MCQQuestion, OpenQuestion, TrueFalseQuestion,
    Answers, AnswerEntry,
)

def _quiz_with_one_of_each() -> Quiz:
    return Quiz(
        pr_number=1,
        questions=[
            MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="B"),
            OpenQuestion(id="q2", prompt="?", rubric="r"),
            TrueFalseQuestion(id="q3", prompt="?", answer=True),
        ],
    )

def test_deterministic_correct():
    quiz = _quiz_with_one_of_each()
    ans = Answers(pr_number=1, entries=[
        AnswerEntry(question_id="q1", value="B"),
        AnswerEntry(question_id="q2", value="long answer"),
        AnswerEntry(question_id="q3", value="true"),
    ])
    res = grade(quiz, ans, llm=FakeLLM(canned_open_score=80, canned_open_feedback="ok"))
    by = {r.question_id: r for r in res.per_question}
    assert by["q1"].correct and by["q1"].score == 100
    assert by["q2"].score == 80 and by["q2"].feedback == "ok"
    assert by["q3"].correct
    # total = (100 + 80 + 100) / 3 = 93
    assert res.total_score == 93

def test_deterministic_wrong():
    quiz = _quiz_with_one_of_each()
    ans = Answers(pr_number=1, entries=[
        AnswerEntry(question_id="q1", value="A"),
        AnswerEntry(question_id="q2", value=""),
        AnswerEntry(question_id="q3", value="false"),
    ])
    res = grade(quiz, ans, llm=FakeLLM(canned_open_score=10, canned_open_feedback="no"))
    by = {r.question_id: r for r in res.per_question}
    assert not by["q1"].correct and by["q1"].score == 0
    assert by["q2"].score == 10
    assert not by["q3"].correct
```

- [ ] **Step 2: Implement**

```python
# src/cognit/engine/grade.py
from cognit.engine.llm import LLMClient
from cognit.engine.models import (
    Quiz, Answers, Results, QuestionResult,
    MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion,
)


def grade(quiz: Quiz, answers: Answers, *, llm: LLMClient) -> Results:
    by_id = {e.question_id: e.value for e in answers.entries}
    per: list[QuestionResult] = []
    for q in quiz.questions:
        v = by_id.get(q.id, "")
        if isinstance(q, (MCQQuestion, MermaidQuestion)):
            ok = v == q.answer
            per.append(QuestionResult(
                question_id=q.id, correct=ok, score=100 if ok else 0, feedback="",
            ))
        elif isinstance(q, TrueFalseQuestion):
            ok = v.strip().lower() == ("true" if q.answer else "false")
            per.append(QuestionResult(
                question_id=q.id, correct=ok, score=100 if ok else 0, feedback="",
            ))
        elif isinstance(q, OpenQuestion):
            score, fb = llm.grade_open(q.prompt, q.rubric, v)
            per.append(QuestionResult(
                question_id=q.id, correct=score >= 70, score=score, feedback=fb,
            ))
    total = sum(r.score for r in per) // len(per) if per else 0
    return Results(pr_number=quiz.pr_number, total_score=total, per_question=per)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/engine/test_grade.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/engine/grade.py tests/engine/test_grade.py
git commit -m "feat(engine): grading (deterministic + LLM for open Q)"
```

### Task M1.8: Dev script to exercise engine end-to-end

**Files:**
- Create: `scripts/dev_generate.py`

- [ ] **Step 1: Write the script**

```python
# scripts/dev_generate.py
"""Manual smoke test. Usage: uv run python scripts/dev_generate.py <diff-file>"""
import sys
from pathlib import Path
from cognit.engine.generate import generate_quiz
from cognit.engine.llm_githubmodels import GitHubModelsLLM

if __name__ == "__main__":
    diff = Path(sys.argv[1]).read_text()
    quiz = generate_quiz(
        diff=diff, pr_title="dev test", pr_body="", files={},
        pr_number=0, llm=GitHubModelsLLM(),
    )
    print(quiz.model_dump_json(indent=2))
```

- [ ] **Step 2: Run with a real diff (requires GITHUB_TOKEN with models scope)**

```bash
GITHUB_TOKEN=<token> uv run python scripts/dev_generate.py tests/fixtures/diffs/small_refactor.patch
```
Expected: valid Quiz JSON to stdout.

- [ ] **Step 3: Commit**

```bash
git add scripts/dev_generate.py
git commit -m "chore: dev script for manual engine smoke testing"
```

---

## M2 — Comment serialization

### Task M2.1: Render Quiz to markdown

**Files:**
- Create: `src/cognit/comment/__init__.py`, `src/cognit/comment/render.py`, `tests/comment/__init__.py`, `tests/comment/test_render.py`

- [ ] **Step 1: Write failing test**

```python
# tests/comment/test_render.py
from cognit.comment.render import render_quiz
from cognit.engine.models import Quiz, MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion

def _sample_quiz() -> Quiz:
    return Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="Why X?", options=["A","B","C"], answer="B"),
            MermaidQuestion(
                id="q2", prompt="Pick the flow:",
                options={"A": "flowchart LR\nA-->B", "B": "flowchart LR\nB-->A"}, answer="A",
            ),
            OpenQuestion(id="q3", prompt="Explain.", rubric="mentions safety"),
            TrueFalseQuestion(id="q4", prompt="Is it?", answer=True),
        ],
    )

def test_render_includes_marker():
    md = render_quiz(_sample_quiz())
    assert "<!-- cognit:quiz v1 -->" in md

def test_render_mermaid_uses_code_fence():
    md = render_quiz(_sample_quiz())
    assert "```mermaid" in md
    assert "flowchart LR" in md

def test_render_embeds_json_state():
    md = render_quiz(_sample_quiz())
    assert "```json" in md
    assert '"pr_number": 42' in md
```

- [ ] **Step 2: Implement**

```python
# src/cognit/comment/render.py
from cognit.engine.models import (
    Quiz, Answers, Results,
    MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion,
)

_MARKER_QUIZ = "<!-- cognit:quiz v1 -->"
_MARKER_ANSWERS = "<!-- cognit:answers v1 -->"
_MARKER_RESULTS = "<!-- cognit:results v1 -->"


def render_quiz(quiz: Quiz) -> str:
    parts = [_MARKER_QUIZ, "## Quiz on your PR", "", "Take it: `cognit take` or scroll down.", ""]
    for i, q in enumerate(quiz.questions, 1):
        parts.append(f"### Question {i} — {q.type}")
        parts.append(q.prompt)
        parts.append("")
        if isinstance(q, MCQQuestion):
            for label in q.options:
                parts.append(f"- {label}")
        elif isinstance(q, MermaidQuestion):
            for label, src in q.options.items():
                parts.append(f"#### Option {label}")
                parts.append("```mermaid")
                parts.append(src)
                parts.append("```")
        elif isinstance(q, TrueFalseQuestion):
            parts.append("- true / false")
        # open: just the prompt
        parts.append("")
    parts.append("---")
    parts.append("<details><summary>Quiz state (used by the CLI)</summary>")
    parts.append("")
    parts.append("```json")
    parts.append(quiz.model_dump_json(indent=2))
    parts.append("```")
    parts.append("</details>")
    return "\n".join(parts)


def render_answers(ans: Answers, deterministic_score: int) -> str:
    return (
        f"{_MARKER_ANSWERS}\n"
        f"## My answers\n\n"
        f"Deterministic-grade score (MCQ + mermaid + T/F): **{deterministic_score}%**\n\n"
        f"```json\n{ans.model_dump_json(indent=2)}\n```\n"
    )


def render_results(res: Results) -> str:
    lines = [_MARKER_RESULTS, "## Quiz results", "", f"**Total: {res.total_score}%**", ""]
    for r in res.per_question:
        icon = "✅" if r.correct else "❌"
        lines.append(f"- {icon} `{r.question_id}` — {r.score}%")
        if r.feedback:
            lines.append(f"  > {r.feedback}")
    return "\n".join(lines)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/comment/test_render.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/comment/ tests/comment/
git commit -m "feat(comment): render Quiz/Answers/Results to markdown"
```

### Task M2.2: Parse markdown back into models + roundtrip tests

**Files:**
- Create: `src/cognit/comment/parse.py`, `tests/comment/test_roundtrip.py`

- [ ] **Step 1: Write roundtrip tests**

```python
# tests/comment/test_roundtrip.py
from cognit.comment.render import render_quiz, render_answers, render_results
from cognit.comment.parse import parse_quiz, parse_answers, parse_results
from cognit.engine.models import (
    Quiz, Answers, Results, AnswerEntry, QuestionResult,
    MCQQuestion, OpenQuestion,
)

def _sample_quiz() -> Quiz:
    return Quiz(pr_number=7, questions=[
        MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="B"),
        OpenQuestion(id="q2", prompt="?", rubric="r"),
    ])

def test_quiz_roundtrip():
    q = _sample_quiz()
    assert parse_quiz(render_quiz(q)) == q

def test_quiz_parse_finds_block_amid_user_edits():
    q = _sample_quiz()
    md = "Some prefix\n" + render_quiz(q) + "\n\nUser appended text"
    assert parse_quiz(md) == q

def test_answers_roundtrip():
    a = Answers(pr_number=7, entries=[AnswerEntry(question_id="q1", value="B")])
    assert parse_answers(render_answers(a, 100)) == a

def test_results_roundtrip():
    r = Results(pr_number=7, total_score=85,
                per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")])
    # results renders as markdown (lossy by design — for display only)
    # so just check key fields appear
    md = render_results(r)
    parsed = parse_results(md)
    assert parsed.total_score == 85
    assert parsed.per_question[0].question_id == "q1"
```

- [ ] **Step 2: Implement parser**

```python
# src/cognit/comment/parse.py
import json
import re
from cognit.engine.models import Quiz, Answers, Results, QuestionResult

_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _extract_json(md: str, marker: str) -> str:
    if marker not in md:
        raise ValueError(f"marker {marker!r} not found")
    after = md.split(marker, 1)[1]
    m = _JSON_BLOCK.search(after)
    if not m:
        raise ValueError(f"no json block after {marker!r}")
    return m.group(1)


def parse_quiz(md: str) -> Quiz:
    return Quiz.model_validate_json(_extract_json(md, "<!-- cognit:quiz v1 -->"))


def parse_answers(md: str) -> Answers:
    return Answers.model_validate_json(_extract_json(md, "<!-- cognit:answers v1 -->"))


def parse_results(md: str) -> Results:
    # Results markdown is for humans; parser is lossy/best-effort.
    if "<!-- cognit:results v1 -->" not in md:
        raise ValueError("not a results comment")
    total = 0
    m = re.search(r"\*\*Total:\s*(\d+)%\*\*", md)
    if m:
        total = int(m.group(1))
    per: list[QuestionResult] = []
    for line in md.splitlines():
        m2 = re.match(r"- (✅|❌) `([^`]+)` — (\d+)%", line)
        if m2:
            per.append(QuestionResult(
                question_id=m2.group(2),
                correct=m2.group(1) == "✅",
                score=int(m2.group(3)),
                feedback="",
            ))
    return Results(pr_number=0, total_score=total, per_question=per)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/comment/ -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/comment/parse.py tests/comment/test_roundtrip.py
git commit -m "feat(comment): parse Quiz/Answers/Results from markdown + roundtrip tests"
```

---

## M3 — CLI scaffold + `cognit generate`

### Task M3.1: typer scaffold

**Files:**
- Create: `src/cognit/cli/__init__.py`, `src/cognit/cli/version.py`, `tests/cli/__init__.py`, `tests/cli/test_root.py`

- [ ] **Step 1: Write failing test**

```python
# tests/cli/test_root.py
from typer.testing import CliRunner
from cognit.cli import app

runner = CliRunner()

def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "cognit" in result.stdout.lower()

def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert "take" in result.stdout
    assert "generate" in result.stdout
    assert "grade" in result.stdout
```

- [ ] **Step 2: Implement scaffold**

```python
# src/cognit/cli/version.py
__version__ = "0.1.0"
```

```python
# src/cognit/cli/__init__.py
import typer
from cognit.cli.version import __version__

app = typer.Typer(no_args_is_help=True, help="PR-author comprehension quiz tool")


def _version_callback(value: bool):
    if value:
        typer.echo(f"cognit {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
):
    pass


# subcommand registration (stubs for now, real impl added below)
@app.command()
def take():
    """Take a quiz on a PR."""
    typer.echo("take: not implemented yet")


@app.command()
def generate():
    """Generate a quiz on a PR (used by the GitHub Action)."""
    typer.echo("generate: not implemented yet")


@app.command()
def grade():
    """Grade submitted answers (used by the GitHub Action)."""
    typer.echo("grade: not implemented yet")
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/cli/test_root.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/cli/ tests/cli/
git commit -m "feat(cli): typer scaffold with stub subcommands"
```

### Task M3.2: ghio.pr — fetch PR metadata via `gh`

**Files:**
- Create: `src/cognit/ghio/__init__.py`, `src/cognit/ghio/pr.py`, `tests/ghio/__init__.py`, `tests/ghio/test_pr.py`

- [ ] **Step 1: Write failing test using a fake `gh` shim**

```python
# tests/ghio/test_pr.py
import json
import os
import stat
from pathlib import Path
import pytest
from cognit.ghio.pr import fetch_pr_info, PRInfo


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """Place a fake `gh` on PATH that returns canned JSON."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    payload = {
        "number": 42,
        "title": "Add caching",
        "body": "Adds an in-memory cache.",
        "headRepository": {"nameWithOwner": "acme/repo"},
        "headRefName": "feat/cache",
        "author": {"login": "alice"},
    }
    fake.write_text(f"#!/bin/sh\necho '{json.dumps(payload)}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return payload


def test_fetch_pr_info(fake_gh):
    info = fetch_pr_info("https://github.com/acme/repo/pull/42")
    assert info == PRInfo(
        number=42,
        title="Add caching",
        body="Adds an in-memory cache.",
        repo="acme/repo",
        branch="feat/cache",
        author="alice",
    )
```

- [ ] **Step 2: Implement**

```python
# src/cognit/ghio/pr.py
import json
import subprocess
from dataclasses import dataclass


@dataclass
class PRInfo:
    number: int
    title: str
    body: str
    repo: str  # owner/name
    branch: str
    author: str


def fetch_pr_info(pr_url_or_number: str) -> PRInfo:
    """Fetch PR metadata via `gh pr view --json ...`."""
    result = subprocess.run(
        [
            "gh", "pr", "view", pr_url_or_number,
            "--json", "number,title,body,headRepository,headRefName,author",
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return PRInfo(
        number=data["number"],
        title=data["title"],
        body=data["body"] or "",
        repo=data["headRepository"]["nameWithOwner"],
        branch=data["headRefName"],
        author=data["author"]["login"],
    )


def post_comment(pr_url_or_number: str, body: str) -> None:
    subprocess.run(
        ["gh", "pr", "comment", pr_url_or_number, "--body", body],
        check=True,
    )


def list_comments(pr_url_or_number: str) -> list[dict]:
    result = subprocess.run(
        ["gh", "pr", "view", pr_url_or_number, "--json", "comments"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["comments"]
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/ghio/test_pr.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/ghio/ tests/ghio/
git commit -m "feat(ghio): PR metadata fetch and comment ops via gh CLI"
```

### Task M3.3: ghio.diff — fetch diff + touched files

**Files:**
- Create: `src/cognit/ghio/diff.py`, `tests/ghio/test_diff.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ghio/test_diff.py
import os
import stat
from pathlib import Path
import pytest
from cognit.ghio.diff import fetch_diff_and_files


@pytest.fixture
def fake_gh_diff(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    # Behavior: respond to `gh pr diff <num>` with a hardcoded diff,
    # and to `gh pr view <num> --json files` with the touched files list.
    fake.write_text("""#!/bin/sh
case "$3" in
  diff) echo '--- a/cache.py
+++ b/cache.py
@@ -1 +1 @@
-old
+new' ;;
  view)
    if echo "$@" | grep -q files; then
      echo '{"files":[{"path":"cache.py"}]}'
    else
      echo '{}'
    fi ;;
esac
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    # Place a fake repo file the function will try to read via `git show`
    # Tests will need a separate `git` shim if we go that route — for now stub via file reads.


def test_diff_returns_unified_diff(fake_gh_diff):
    diff, files = fetch_diff_and_files("42", fetch_file_contents=lambda path: "fake content")
    assert "+++ b/cache.py" in diff
    assert files == {"cache.py": "fake content"}
```

- [ ] **Step 2: Implement**

```python
# src/cognit/ghio/diff.py
import json
import subprocess
from typing import Callable


def fetch_diff_and_files(
    pr_url_or_number: str,
    *,
    fetch_file_contents: Callable[[str], str],
) -> tuple[str, dict[str, str]]:
    """Return (unified diff, {path: full_content_at_head})."""
    diff = subprocess.run(
        ["gh", "pr", "diff", pr_url_or_number],
        capture_output=True, text=True, check=True,
    ).stdout

    files_json = subprocess.run(
        ["gh", "pr", "view", pr_url_or_number, "--json", "files"],
        capture_output=True, text=True, check=True,
    ).stdout
    paths = [f["path"] for f in json.loads(files_json)["files"]]

    contents: dict[str, str] = {}
    for p in paths:
        try:
            contents[p] = fetch_file_contents(p)
        except Exception:
            contents[p] = ""
    return diff, contents


def read_file_at_head(path: str) -> str:
    """Default file fetcher: read from local checkout (used in CI after `actions/checkout`)."""
    return subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True, text=True, check=True,
    ).stdout
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/ghio/test_diff.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/ghio/diff.py tests/ghio/test_diff.py
git commit -m "feat(ghio): fetch diff and touched-file contents"
```

### Task M3.4: `cognit generate` command

**Files:**
- Modify: `src/cognit/cli/__init__.py` (replace stub)
- Create: `src/cognit/cli/generate.py`, `tests/cli/test_generate.py`

- [ ] **Step 1: Write failing test using FakeLLM end-to-end**

```python
# tests/cli/test_generate.py
import json
from unittest.mock import patch
from typer.testing import CliRunner
from cognit.cli import app
from cognit.engine.models import Quiz, MCQQuestion
from cognit.engine.llm_fake import FakeLLM
from cognit.ghio.pr import PRInfo

runner = CliRunner()


def test_generate_dry_run_prints_markdown(monkeypatch):
    canned = Quiz(pr_number=42, questions=[
        MCQQuestion(id="q1", prompt="why?", options=["A","B"], answer="A"),
    ])
    monkeypatch.setattr(
        "cognit.ghio.pr.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.ghio.diff.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("diffstr", {}),
    )
    monkeypatch.setattr(
        "cognit.cli.generate._make_llm",
        lambda model: FakeLLM(canned_quiz=canned),
    )
    result = runner.invoke(app, ["generate", "--pr", "https://github.com/o/r/pull/42", "--dry-run"])
    assert result.exit_code == 0
    assert "<!-- cognit:quiz v1 -->" in result.stdout
    assert "why?" in result.stdout
```

- [ ] **Step 2: Implement**

```python
# src/cognit/cli/generate.py
import typer
from cognit.comment.render import render_quiz
from cognit.engine.generate import generate_quiz
from cognit.engine.llm import LLMClient
from cognit.engine.llm_githubmodels import GitHubModelsLLM
from cognit.ghio.pr import fetch_pr_info, post_comment
from cognit.ghio.diff import fetch_diff_and_files, read_file_at_head


def _make_llm(model: str) -> LLMClient:
    return GitHubModelsLLM()


def run(
    pr: str,
    post: bool = False,
    dry_run: bool = False,
    model: str = "gpt-4o-mini",
    min_diff_lines: int = 50,
    max_diff_lines: int = 2000,
) -> None:
    info = fetch_pr_info(pr)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return
    diff, files = fetch_diff_and_files(pr, fetch_file_contents=read_file_at_head)
    diff_lines = diff.count("\n")
    if diff_lines < min_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (< {min_diff_lines}) — skipping.")
        return
    if diff_lines > max_diff_lines:
        typer.echo(f"diff is {diff_lines} lines (> {max_diff_lines}) — skipping.")
        return
    quiz = generate_quiz(
        diff=diff, pr_title=info.title, pr_body=info.body,
        files=files, pr_number=info.number, llm=_make_llm(model),
    )
    md = render_quiz(quiz)
    if dry_run:
        typer.echo(md)
        return
    if post:
        post_comment(pr, md)
        typer.echo("quiz comment posted.")
    else:
        typer.echo(md)
```

```python
# src/cognit/cli/__init__.py  (replace the stub `generate` command)
import typer
from cognit.cli.version import __version__
from cognit.cli import generate as _gen

app = typer.Typer(no_args_is_help=True, help="PR-author comprehension quiz tool")


def _version_callback(value: bool):
    if value:
        typer.echo(f"cognit {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
):
    pass


@app.command()
def take():
    typer.echo("take: not implemented yet")


@app.command()
def generate(
    pr: str = typer.Option(..., "--pr", help="PR URL or number"),
    post: bool = typer.Option(False, "--post", help="Post the quiz as a PR comment"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    model: str = typer.Option("gpt-4o-mini", "--model"),
    min_diff_lines: int = typer.Option(50, "--min-diff-lines"),
    max_diff_lines: int = typer.Option(2000, "--max-diff-lines"),
):
    """Generate a quiz on a PR (used by the GitHub Action)."""
    _gen.run(pr, post=post, dry_run=dry_run, model=model,
             min_diff_lines=min_diff_lines, max_diff_lines=max_diff_lines)


@app.command()
def grade():
    typer.echo("grade: not implemented yet")
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/cli/test_generate.py -v
```
Expected: PASS.

- [ ] **Step 4: Manual smoke test**

```bash
uv tool install --from . cognit
cognit generate --pr <sandbox-pr-url> --dry-run
```

- [ ] **Step 5: Commit**

```bash
git add src/cognit/cli/ tests/cli/
git commit -m "feat(cli): cognit generate orchestrates ghio + engine + comment"
```

---

## M4 — Generator Action

### Task M4.1: Composite action.yml

**Files:**
- Create: `actions/cognit-generate/action.yml`, `.github/examples/cognit-generate.yml`

- [ ] **Step 1: Write the Composite Action**

```yaml
# actions/cognit-generate/action.yml
name: "Cognit Generate"
description: "Generate a PR-author comprehension quiz and post it as a PR comment."

inputs:
  version:
    description: "Pinned cognit PyPI version"
    default: "0.1.0"
  model:
    description: "LLM model to use (e.g. gpt-4o-mini)"
    default: "gpt-4o-mini"
  min-diff-lines:
    description: "Skip PRs below this number of changed lines"
    default: "50"
  max-diff-lines:
    description: "Skip PRs above this number of changed lines"
    default: "2000"

runs:
  using: "composite"
  steps:
    - name: Setup uv
      uses: astral-sh/setup-uv@v3

    - name: Cache npm
      uses: actions/cache@v4
      with:
        path: ~/.npm
        key: npm-mermaid-cli-${{ runner.os }}

    - name: Install mermaid-cli
      shell: bash
      run: npm install -g @mermaid-js/mermaid-cli@10

    - name: Install cognit
      shell: bash
      run: uv tool install "cognit==${{ inputs.version }}"

    - name: Run generator
      shell: bash
      env:
        GITHUB_TOKEN: ${{ env.GITHUB_TOKEN }}
      run: |
        cognit generate \
          --pr "${{ github.event.pull_request.html_url }}" \
          --post \
          --model "${{ inputs.model }}" \
          --min-diff-lines "${{ inputs.min-diff-lines }}" \
          --max-diff-lines "${{ inputs.max-diff-lines }}"
```

- [ ] **Step 2: Write the example workflow**

```yaml
# .github/examples/cognit-generate.yml
name: Cognit — generate
on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  models: read

jobs:
  cognit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: <your-org>/cognit/actions/cognit-generate@v1
```

- [ ] **Step 3: Commit**

```bash
git add actions/ .github/examples/
git commit -m "feat(actions): generator Composite Action + example workflow"
```

### Task M4.2: act smoke test

**Files:**
- Create: `.github/workflows/ci.yml` (initial)

- [ ] **Step 1: Add CI workflow with an `act`-runnable smoke**

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --all-extras
      - run: uv run ruff check
      - run: uv run mypy
      - run: uv run pytest -v

  action-smoke:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: npm install -g @mermaid-js/mermaid-cli@10
      - run: uv tool install --from . cognit
      - run: cognit --help
      - run: cognit generate --help
```

- [ ] **Step 2: Run locally with act**

```bash
act -j test
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint + typecheck + tests + Action smoke"
```

### Task M4.3: Manual end-to-end on a sandbox repo

- [ ] **Step 1: Create a sandbox repo, add the example workflow, push a PR**
- [ ] **Step 2: Watch the Action run, verify a quiz comment is posted**
- [ ] **Step 3: Document any rough edges in `docs/known-issues.md`**

---

## M5 — CLI `cognit take` + local web UI

### Task M5.1: `take` subcommand auto-detects PR

**Files:**
- Create: `src/cognit/cli/take.py`, `tests/cli/test_take.py`
- Modify: `src/cognit/cli/__init__.py` (replace stub)

- [ ] **Step 1: Write failing test**

```python
# tests/cli/test_take.py
from typer.testing import CliRunner
from cognit.cli import app

runner = CliRunner()


def test_take_requires_pr_or_branch(monkeypatch):
    monkeypatch.setattr("cognit.cli.take._detect_pr_from_branch", lambda: None)
    result = runner.invoke(app, ["take"])
    assert result.exit_code != 0
    assert "no PR" in result.stdout.lower()


def test_take_auto_detects(monkeypatch):
    monkeypatch.setattr(
        "cognit.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/42",
    )
    monkeypatch.setattr(
        "cognit.cli.take._run_take_flow",
        lambda pr_url, show_results_only: None,
    )
    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Implement skeleton**

```python
# src/cognit/cli/take.py
import subprocess
import typer


def _detect_pr_from_branch() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "url"],
            capture_output=True, text=True, check=True,
        )
        import json
        return json.loads(result.stdout)["url"]
    except subprocess.CalledProcessError:
        return None


def _run_take_flow(pr_url: str, show_results_only: bool) -> None:
    raise NotImplementedError  # filled in M5.2+


def run(pr: str | None, show_results: bool) -> None:
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)
    _run_take_flow(pr_url, show_results_only=show_results)
```

```python
# src/cognit/cli/__init__.py  (replace the stub `take`)
# ... add at top of file:
from cognit.cli import take as _take

# replace the stub command:
@app.command()
def take(
    pr: str | None = typer.Option(None, "--pr", help="PR URL (default: auto-detect)"),
    show_results: bool = typer.Option(False, "--show-results"),
):
    """Take a quiz on a PR locally."""
    _take.run(pr, show_results=show_results)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/cli/test_take.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/cli/take.py tests/cli/test_take.py src/cognit/cli/__init__.py
git commit -m "feat(cli): take command scaffold with PR auto-detect"
```

### Task M5.2: Find quiz comment in PR

**Files:**
- Modify: `src/cognit/ghio/pr.py`, `tests/ghio/test_pr.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ghio/test_pr.py  (add)
def test_find_quiz_comment(monkeypatch):
    from cognit.ghio.pr import find_latest_marker_comment
    monkeypatch.setattr(
        "cognit.ghio.pr.list_comments",
        lambda pr: [
            {"body": "drive-by", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "<!-- cognit:quiz v1 -->\n```json\n{...}\n```", "createdAt": "2026-01-02T00:00:00Z"},
            {"body": "<!-- cognit:quiz v1 -->\nnewer", "createdAt": "2026-01-03T00:00:00Z"},
        ],
    )
    c = find_latest_marker_comment("123", "<!-- cognit:quiz v1 -->")
    assert c is not None
    assert "newer" in c
```

- [ ] **Step 2: Implement**

```python
# src/cognit/ghio/pr.py  (add)
def find_latest_marker_comment(pr_url_or_number: str, marker: str) -> str | None:
    comments = list_comments(pr_url_or_number)
    matching = [c for c in comments if marker in c["body"]]
    if not matching:
        return None
    matching.sort(key=lambda c: c["createdAt"], reverse=True)
    return matching[0]["body"]
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/ghio/ -v
git add src/cognit/ghio/pr.py tests/ghio/test_pr.py
git commit -m "feat(ghio): find_latest_marker_comment"
```

### Task M5.3: FastAPI app skeleton with embedded assets

**Files:**
- Create: `src/cognit/server/__init__.py`, `src/cognit/server/app.py`, `src/cognit/server/assets/index.html`, `src/cognit/server/assets/quiz.js`, `src/cognit/server/assets/styles.css`, `tests/server/__init__.py`, `tests/server/test_app.py`
- Modify: `pyproject.toml` (add fastapi + uvicorn)

- [ ] **Step 1: Add deps**

```bash
uv add fastapi uvicorn
```

- [ ] **Step 2: Write failing test**

```python
# tests/server/test_app.py
from fastapi.testclient import TestClient
from cognit.engine.models import Quiz, MCQQuestion
from cognit.server.app import build_app


def test_get_root_renders_quiz():
    quiz = Quiz(pr_number=42, questions=[
        MCQQuestion(id="q1", prompt="why?", options=["A","B"], answer="A"),
    ])
    app = build_app(quiz=quiz, pr_url="https://github.com/o/r/pull/42", post_answers=lambda md: None)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "why?" in r.text
    assert b"<!doctype html>" in r.content.lower()


def test_static_assets_served():
    quiz = Quiz(pr_number=1, questions=[])
    app = build_app(quiz=quiz, pr_url="x", post_answers=lambda md: None)
    client = TestClient(app)
    assert client.get("/static/quiz.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
```

- [ ] **Step 3: Implement (minimal)**

```html
<!-- src/cognit/server/assets/index.html -->
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Quiz — PR #__PR__</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <h1>Quiz for PR #__PR__</h1>
  <div id="quiz"></div>
  <button id="submit">Submit</button>
  <pre id="result"></pre>
  <script type="module">
    window.QUIZ = __QUIZ_JSON__;
    window.PR_URL = "__PR_URL__";
  </script>
  <script type="module" src="/static/quiz.js"></script>
</body>
</html>
```

```javascript
// src/cognit/server/assets/quiz.js
import mermaid from "/static/mermaid.esm.min.js";
mermaid.initialize({ startOnLoad: false });

const quiz = window.QUIZ;
const root = document.getElementById("quiz");

function render() {
  for (const q of quiz.questions) {
    const section = document.createElement("section");
    section.innerHTML = `<h3>${q.id} — ${q.type}</h3><p>${q.prompt}</p>`;
    if (q.type === "mcq") {
      for (const opt of q.options) {
        section.innerHTML += `<label><input type="radio" name="${q.id}" value="${opt}"> ${opt}</label><br>`;
      }
    } else if (q.type === "mermaid") {
      for (const [label, src] of Object.entries(q.options)) {
        const id = `${q.id}_${label}`;
        section.innerHTML += `<label><input type="radio" name="${q.id}" value="${label}"> Option ${label}</label><div class="mermaid" id="${id}">${src}</div>`;
      }
    } else if (q.type === "open") {
      section.innerHTML += `<textarea name="${q.id}" rows="6" cols="80"></textarea>`;
    } else if (q.type === "tf") {
      section.innerHTML += `<label><input type="radio" name="${q.id}" value="true"> true</label><label><input type="radio" name="${q.id}" value="false"> false</label>`;
    }
    root.appendChild(section);
  }
  mermaid.run();
}

document.getElementById("submit").addEventListener("click", async () => {
  const entries = quiz.questions.map(q => {
    const el = document.querySelector(`[name="${q.id}"]:checked`) || document.querySelector(`[name="${q.id}"]`);
    return { question_id: q.id, value: el ? el.value : "" };
  });
  const resp = await fetch("/submit", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ version: "1", pr_number: quiz.pr_number, entries }),
  });
  const data = await resp.json();
  document.getElementById("result").textContent = JSON.stringify(data, null, 2);
});

render();
```

```css
/* src/cognit/server/assets/styles.css */
body { font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
section { border: 1px solid #ccc; padding: 1rem; margin: 1rem 0; border-radius: 6px; }
.mermaid { background: #f6f8fa; padding: 0.5rem; }
button { padding: 0.6rem 1.2rem; font-size: 1rem; }
```

```python
# src/cognit/server/app.py
import json
from importlib import resources
from typing import Callable
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from cognit.engine.models import Quiz, Answers
from cognit.engine.grade import grade
from cognit.engine.llm_fake import FakeLLM


def _assets_dir():
    return resources.files("cognit.server.assets")


def build_app(
    *,
    quiz: Quiz,
    pr_url: str,
    post_answers: Callable[[str], None],
) -> FastAPI:
    app = FastAPI()
    assets = _assets_dir()
    app.mount("/static", StaticFiles(directory=str(assets)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (assets / "index.html").read_text()
        html = (
            html.replace("__PR__", str(quiz.pr_number))
                .replace("__PR_URL__", pr_url)
                .replace("__QUIZ_JSON__", quiz.model_dump_json())
        )
        return HTMLResponse(html)

    @app.post("/submit")
    async def submit(req: Request) -> JSONResponse:
        body = await req.json()
        answers = Answers.model_validate(body)
        # deterministic grading immediately (open Q scored 0 here; CI will re-grade)
        results = grade(quiz, answers, llm=FakeLLM(canned_open_score=0, canned_open_feedback="awaiting CI"))
        from cognit.comment.render import render_answers
        det_score = sum(
            r.score for r in results.per_question
            if any(q.id == r.question_id and q.type != "open" for q in quiz.questions)
        ) // max(1, sum(1 for q in quiz.questions if q.type != "open"))
        md = render_answers(answers, deterministic_score=det_score)
        post_answers(md)
        return JSONResponse({"deterministic_score": det_score, "per_question": [r.model_dump() for r in results.per_question]})

    return app
```

- [ ] **Step 4: Add mermaid.js to assets**

```bash
curl -L https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs \
  -o src/cognit/server/assets/mermaid.esm.min.js
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/server/ -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cognit/server/ tests/server/ pyproject.toml uv.lock
git commit -m "feat(server): FastAPI app + embedded quiz UI"
```

### Task M5.4: Wire `take` to the server, launch browser, poll for results

**Files:**
- Modify: `src/cognit/cli/take.py`
- Modify: `tests/cli/test_take.py`

- [ ] **Step 1: Write failing test for the orchestration**

```python
# tests/cli/test_take.py  (add)
def test_take_flow_fetches_parses_and_runs_server(monkeypatch):
    from cognit.engine.models import Quiz, MCQQuestion
    from cognit.comment.render import render_quiz
    quiz = Quiz(pr_number=42, questions=[
        MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="A"),
    ])
    monkeypatch.setattr(
        "cognit.ghio.pr.find_latest_marker_comment",
        lambda pr, marker: render_quiz(quiz),
    )
    captured = {}
    def fake_serve(quiz_, pr_url, post_answers):
        captured["quiz"] = quiz_
        captured["pr_url"] = pr_url
    monkeypatch.setattr("cognit.cli.take._serve_blocking", fake_serve)

    from cognit.cli.take import _run_take_flow
    _run_take_flow("https://github.com/o/r/pull/42", show_results_only=False)
    assert captured["quiz"] == quiz
    assert captured["pr_url"] == "https://github.com/o/r/pull/42"
```

- [ ] **Step 2: Implement orchestration + browser launch**

```python
# src/cognit/cli/take.py  (replace _run_take_flow + add helpers)
import socket
import threading
import time
import webbrowser
import json
import subprocess
import typer
import uvicorn

from cognit.comment.parse import parse_quiz, parse_results
from cognit.ghio.pr import find_latest_marker_comment, post_comment
from cognit.server.app import build_app


_MARKER_QUIZ = "<!-- cognit:quiz v1 -->"
_MARKER_RESULTS = "<!-- cognit:results v1 -->"


def _detect_pr_from_branch() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "url"],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)["url"]
    except subprocess.CalledProcessError:
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_blocking(quiz, pr_url: str, post_answers) -> None:
    app = build_app(quiz=quiz, pr_url=pr_url, post_answers=post_answers)
    port = _free_port()
    typer.echo(f"opening http://127.0.0.1:{port} in your browser...")
    threading.Thread(target=lambda: webbrowser.open(f"http://127.0.0.1:{port}"), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _run_take_flow(pr_url: str, show_results_only: bool) -> None:
    if show_results_only:
        results_md = find_latest_marker_comment(pr_url, _MARKER_RESULTS)
        if results_md is None:
            typer.echo("no results yet; the grader Action may still be running.")
            raise typer.Exit(code=1)
        typer.echo(parse_results(results_md).model_dump_json(indent=2))
        return

    quiz_md = find_latest_marker_comment(pr_url, _MARKER_QUIZ)
    if quiz_md is None:
        typer.echo("no quiz comment found on this PR — has the generator Action run?")
        raise typer.Exit(code=1)
    quiz = parse_quiz(quiz_md)
    _serve_blocking(
        quiz, pr_url,
        post_answers=lambda md: post_comment(pr_url, md),
    )


def run(pr: str | None, show_results: bool) -> None:
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)
    _run_take_flow(pr_url, show_results_only=show_results)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/cli/test_take.py -v
```
Expected: PASS.

- [ ] **Step 4: Manual e2e**

```bash
# in a sandbox repo with a quiz comment present
cognit take
```
Expected: browser opens; quiz renders; submitting posts an answers comment.

- [ ] **Step 5: Commit**

```bash
git add src/cognit/cli/take.py tests/cli/test_take.py
git commit -m "feat(cli): take orchestrates fetch → server → post-answers"
```

### Task M5.5: Polling for results in the browser

**Files:**
- Modify: `src/cognit/server/app.py`, `src/cognit/server/assets/quiz.js`

- [ ] **Step 1: Add `/results` endpoint**

```python
# src/cognit/server/app.py  (add inside build_app)
from cognit.comment.parse import parse_results
from cognit.ghio.pr import find_latest_marker_comment

@app.get("/results")
def results_endpoint() -> JSONResponse:
    md = find_latest_marker_comment(pr_url, "<!-- cognit:results v1 -->")
    if md is None:
        return JSONResponse({"ready": False})
    return JSONResponse({"ready": True, "results": parse_results(md).model_dump()})
```

- [ ] **Step 2: Add polling in `quiz.js`**

Append to `quiz.js` after the submit handler:

```javascript
async function pollResults() {
  for (let i = 0; i < 120; i++) {  // ~5 minutes
    const r = await fetch("/results");
    const data = await r.json();
    if (data.ready) {
      document.getElementById("result").textContent =
        "FINAL: " + JSON.stringify(data.results, null, 2);
      return;
    }
    await new Promise(r => setTimeout(r, 2500));
  }
  document.getElementById("result").textContent =
    "Results not back after 5 minutes — run `cognit take --show-results` later.";
}

document.getElementById("submit").addEventListener("click", () => {
  setTimeout(pollResults, 1000);
});
```

- [ ] **Step 3: Smoke test (manual)**

Open a PR with a quiz, take it, observe the browser poll until the grader Action posts results.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/server/
git commit -m "feat(server): /results endpoint + browser polling"
```

---

## M6 — Grader Action + `cognit grade`

### Task M6.1: `cognit grade` command

**Files:**
- Create: `src/cognit/cli/grade.py`, `tests/cli/test_grade.py`
- Modify: `src/cognit/cli/__init__.py`

- [ ] **Step 1: Write failing test**

```python
# tests/cli/test_grade.py
from typer.testing import CliRunner
from cognit.cli import app
from cognit.engine.models import Quiz, MCQQuestion, OpenQuestion, Answers, AnswerEntry
from cognit.engine.llm_fake import FakeLLM
from cognit.comment.render import render_quiz, render_answers

runner = CliRunner()


def test_grade_command_posts_results(monkeypatch):
    quiz = Quiz(pr_number=42, questions=[
        MCQQuestion(id="q1", prompt="?", options=["A","B"], answer="A"),
        OpenQuestion(id="q2", prompt="?", rubric="r"),
    ])
    answers = Answers(pr_number=42, entries=[
        AnswerEntry(question_id="q1", value="A"),
        AnswerEntry(question_id="q2", value="my answer"),
    ])
    monkeypatch.setattr(
        "cognit.ghio.pr.find_latest_marker_comment",
        lambda pr, marker: render_quiz(quiz) if "quiz" in marker else render_answers(answers, 100),
    )
    posted: list[str] = []
    monkeypatch.setattr("cognit.ghio.pr.post_comment", lambda pr, md: posted.append(md))
    monkeypatch.setattr(
        "cognit.cli.grade._make_llm",
        lambda model: FakeLLM(canned_open_score=85, canned_open_feedback="solid"),
    )
    result = runner.invoke(app, ["grade", "--pr", "https://github.com/o/r/pull/42"])
    assert result.exit_code == 0
    assert posted, "expected a results comment to be posted"
    assert "<!-- cognit:results v1 -->" in posted[0]
    assert "85%" in posted[0] or "solid" in posted[0]
```

- [ ] **Step 2: Implement**

```python
# src/cognit/cli/grade.py
import typer
from cognit.comment.parse import parse_quiz, parse_answers
from cognit.comment.render import render_results
from cognit.engine.grade import grade
from cognit.engine.llm import LLMClient
from cognit.engine.llm_githubmodels import GitHubModelsLLM
from cognit.ghio.pr import find_latest_marker_comment, post_comment


def _make_llm(model: str) -> LLMClient:
    return GitHubModelsLLM()


def run(pr: str, model: str = "gpt-4o-mini") -> None:
    quiz_md = find_latest_marker_comment(pr, "<!-- cognit:quiz v1 -->")
    answers_md = find_latest_marker_comment(pr, "<!-- cognit:answers v1 -->")
    if not (quiz_md and answers_md):
        typer.echo("missing quiz or answers comment — nothing to grade.")
        return
    quiz = parse_quiz(quiz_md)
    answers = parse_answers(answers_md)
    results = grade(quiz, answers, llm=_make_llm(model))
    post_comment(pr, render_results(results))
    typer.echo(f"results posted: total {results.total_score}%")
```

```python
# src/cognit/cli/__init__.py  (replace stub `grade`)
from cognit.cli import grade as _grade

@app.command()
def grade(
    pr: str = typer.Option(..., "--pr"),
    model: str = typer.Option("gpt-4o-mini", "--model"),
):
    """Grade submitted answers (used by the GitHub Action)."""
    _grade.run(pr, model=model)
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/cli/test_grade.py -v
git add src/cognit/cli/
git commit -m "feat(cli): cognit grade orchestrates engine + comment + ghio"
```

### Task M6.2: Grader Composite action + example listener workflow

**Files:**
- Create: `actions/cognit-grade/action.yml`, `.github/examples/cognit-grade.yml`

- [ ] **Step 1: Write the Composite Action**

```yaml
# actions/cognit-grade/action.yml
name: "Cognit Grade"
description: "Grade submitted answers comment and post results."

inputs:
  version:
    default: "0.1.0"
  model:
    default: "gpt-4o-mini"

runs:
  using: "composite"
  steps:
    - uses: astral-sh/setup-uv@v3
    - shell: bash
      run: uv tool install "cognit==${{ inputs.version }}"
    - shell: bash
      env:
        GITHUB_TOKEN: ${{ env.GITHUB_TOKEN }}
      run: |
        cognit grade \
          --pr "${{ github.event.issue.pull_request.url || github.event.issue.html_url }}" \
          --model "${{ inputs.model }}"
```

- [ ] **Step 2: Write the listener workflow**

```yaml
# .github/examples/cognit-grade.yml
name: Cognit — grade
on:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: write
  models: read

jobs:
  grade:
    if: >
      github.event.issue.pull_request &&
      contains(github.event.comment.body, '<!-- cognit:answers v1 -->') &&
      github.event.comment.user.login == github.event.issue.user.login
    runs-on: ubuntu-latest
    steps:
      - uses: <your-org>/cognit/actions/cognit-grade@v1
```

- [ ] **Step 3: Commit**

```bash
git add actions/cognit-grade/ .github/examples/cognit-grade.yml
git commit -m "feat(actions): grader Composite Action + listener workflow"
```

### Task M6.3: End-to-end manual test on sandbox repo

- [ ] **Step 1: Add both example workflows to a sandbox repo, install both Actions**
- [ ] **Step 2: Open a PR; watch the quiz comment appear; run `cognit take`; submit**
- [ ] **Step 3: Watch the answers comment trigger the grader; verify results comment**
- [ ] **Step 4: Verify polling in `cognit take` displays the results**

---

## M7 — Release polish

### Task M7.1: README with quickstart

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**

````markdown
# cognit

> Voluntary, opt-in PR-author comprehension quizzes. Surface the gap between what you think your code does and what it actually does — before you merge.

## What this is

A GitHub-friendly tool that quizzes the **author** of a PR (not the reviewer) on the code they're about to merge. Three pieces:
- A **generator GitHub Action** that posts a quiz comment when you open a PR.
- A **CLI** (`cognit take`) that opens the quiz in your local browser.
- A **grader GitHub Action** that scores your submitted answers and posts results.

Like CI checks, linters, or pre-commit hooks: opt-in. Failing doesn't block merge — the value is the "aha" when you got something wrong.

## Quickstart

### 1. Install the CLI

```bash
pipx install cognit
# or
uv tool install cognit
```

### 2. Add the two workflows to your repo

Copy `.github/examples/cognit-generate.yml` and `.github/examples/cognit-grade.yml` into your repo's `.github/workflows/`.

### 3. Open a PR

The generator runs, posts a quiz comment. Run `cognit take` to take it.

## Status

v1.0 ships the Action+CLI pair. v2 will add a GitHub App (no workflow file needed), a fleet of LLMs for question diversity, and Skills integration for team-specific knowledge injection. See `INTENTS.md`.
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with quickstart"
```

### Task M7.2: Release workflow + GoReleaser-equivalent for PyPI

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write release workflow**

```yaml
# .github/workflows/release.yml
name: release
on:
  push:
    tags: ["v*"]

permissions:
  contents: write
  id-token: write  # trusted publishing to PyPI

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    environment: pypi
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv build
      - uses: pypa/gh-action-pypi-publish@release/v1
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
```

- [ ] **Step 2: Configure trusted publisher in PyPI project settings**
- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: release workflow (PyPI trusted publishing + GH release)"
```

### Task M7.3: LICENSE + CHANGELOG

**Files:**
- Create: `LICENSE` (MIT), `CHANGELOG.md`

- [ ] **Step 1: Add MIT LICENSE**

```text
MIT License

Copyright (c) 2026 <author>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Add CHANGELOG**

```markdown
# Changelog

## [0.1.0] - YYYY-MM-DD

### Added
- Generator GitHub Action: posts a quiz comment on PR open/sync.
- `cognit take` CLI: takes the quiz in a local browser.
- Grader GitHub Action: LLM-grades open questions, posts results.
- Question types: MCQ, mermaid-diagram-pick, open, true/false.
```

- [ ] **Step 3: Commit**

```bash
git add LICENSE CHANGELOG.md
git commit -m "docs: LICENSE (MIT) + CHANGELOG"
```

### Task M7.4: Tag v0.1.0 and ship

- [ ] **Step 1: Verify CI is green on `main`**
- [ ] **Step 2: Update version in `pyproject.toml` and `cli/version.py` to `0.1.0`**
- [ ] **Step 3: Tag**

```bash
git tag -a v0.1.0 -m "v0.1.0 — initial public release"
git push origin v0.1.0
```

- [ ] **Step 4: Watch `release.yml` run, verify PyPI + GitHub Release appear**
- [ ] **Step 5: Test fresh install on a clean machine**

```bash
pipx install cognit
cognit --version
```
Expected: `cognit 0.1.0`.

- [ ] **Step 6: Submit Actions to GitHub Marketplace**

(Manual: go to the repo's Releases page, attach both Actions to the release, fill in marketplace metadata.)

---

## Total effort estimate

~13–18 working days for solo dev. ~2.5–3.5 weeks calendar with normal life.

## Critical path

```
M1 → M2 → M3 → M4 → M5 → M6 → M7
```

No meaningful parallelism for a solo dev. For a team: M1+M2 must precede everything; then M3/M4 are one chain and M5 is independent until late.

## Risks and mitigations

- **LLM prompt quality is the lurking unknown.** No upfront planning replaces iteration on real PRs. M1 ships rough prompts; M7's polish includes a prompt-iteration pass against ~10 real PRs from open-source repos. Snapshot tests on prompts (via `syrupy`) make drift reviewable.
- **Mermaid distractor leak through style.** Already noted in the design. Plan to revisit after first real-use feedback.
- **GitHub Models free-tier rate limits.** 50 high-tier req/day, 150 mini/day. Default to `gpt-4o-mini`. Document path to paid tier or BYOK in README.
- **`mmdc` install latency in CI.** Adds ~30s per Action run. Cache `~/.npm`. Acceptable for MVP.
- **Python startup latency on first CI invocation.** `uv tool install` is fast (~3–5s) but adds to floor. Total cold start before LLM call should stay under 15s.

## What this plan does NOT cover

- Fleet of LLMs (v2)
- Skills integration (v2)
- GitHub App / Marketplace App with hosted backend (v2)
- IDE integration (v3+)
- Reviewer-side mode (v2+)
- Learning history / persistence beyond PR comments (v2)
