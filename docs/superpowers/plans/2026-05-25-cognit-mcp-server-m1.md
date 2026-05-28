# cognit MCP server (Milestone 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone cognit MCP server — the quiz "render API" (`set_quiz`, `replace_question`, `get_answers`, `grade`) plus a browser host and authoritative state — and prove it end-to-end with a **static fixture quiz (no agent yet)**.

**Architecture:** A new `cognit.mcp` package. A long-lived process runs a FastMCP **stdio** server (the agent's render API) on the main thread and a FastAPI **HTTP** server (the browser projection) on a daemon thread, sharing one thread-safe `QuizState` that writes a snapshot through on every mutation. The MCP tools are thin wrappers over pure functions (validate/shuffle, grade) that are unit-tested without `claude`. This is Milestone 1 of the design at `docs/superpowers/specs/2026-05-25-cognit-as-claude-code-plugin-design.md` (§8); the launcher and steering verbs are M2/M3.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI + uvicorn (HTTP host, mirrors existing `server/app.py`), `mcp` / `FastMCP` (stdio MCP server), pytest (`asyncio_mode=auto`), httpx (live-server tests). Reuses `cognit.engine.models`, `cognit.engine.grade`, `cognit.engine.mermaid`, `cognit.engine.generate._neutralize_mermaid_labels`, `cognit.ghio.pr.post_comment`.

---

## File structure

- `src/cognit/mcp/__init__.py` — package marker.
- `src/cognit/mcp/state.py` — `QuizState`: in-memory `{quiz, answers, results}` + write-through snapshot, thread-safe. One responsibility: hold + persist session state.
- `src/cognit/mcp/validate.py` — `validate_and_prepare(draft_dict, pr_number)`: port of the SDK submit-validation hook (shape + per-mermaid checks + missing-explanation) returning `(Quiz | None, failures)`; on success runs the answer-position shuffle. One responsibility: turn an agent-submitted draft into a validated, leak-safe `Quiz`.
- `src/cognit/mcp/grading.py` — `grade_state(state, *, llm)`: build `Answers` from state, call `engine.grade.grade`, store + return `Results`. One responsibility: grading the current answers.
- `src/cognit/mcp/web.py` — `build_web_app(state, *, pr_url, post_comment)`: FastAPI app serving `/state`, `/answer`, `/`, `/static`, `/publish`. One responsibility: the browser projection over `QuizState`.
- `src/cognit/mcp/server.py` — FastMCP tools (`set_quiz`/`replace_question`/`get_answers`/`grade`) + `main()` wiring (env → port/snapshot/PR, start HTTP thread fail-hard, run stdio). One responsibility: the MCP surface + process wiring.
- `src/cognit/mcp/__main__.py` — `python -m cognit.mcp` → `server.main()`.
- `src/cognit/mcp/assets/quiz_mcp.js` — `/state`-polling browser bootstrap + renderers (M1 functional renderer; the polished `server/assets/quiz.js` port is a later refinement).
- `src/cognit/mcp/assets/index.html`, `styles.css` — minimal page + reused styles.
- Tests under `tests/mcp/`: `test_state.py`, `test_validate.py`, `test_grading.py`, `test_web.py`, `test_tools.py`.

---

## Task 1: Scaffold the package, add the `mcp` dependency, prove it imports

**Files:**
- Create: `src/cognit/mcp/__init__.py`
- Create: `src/cognit/mcp/__main__.py`
- Modify: `pyproject.toml:17-24` (add `mcp` to dependencies)
- Test: `tests/mcp/__init__.py`, `tests/mcp/test_import.py`

- [ ] **Step 1: Add the `mcp` dependency**

In `pyproject.toml`, the `dependencies` array (lines 17-24) currently ends with `"claude-agent-sdk>=0.1.44",`. Add a line:

```toml
    "mcp>=1.2",
```

(FastMCP ships in the `mcp` package; it's currently only present transitively via `claude-agent-sdk`. Declare it directly since the MCP server depends on it.)

- [ ] **Step 2: Create the package markers**

`src/cognit/mcp/__init__.py`:

```python
"""cognit MCP server: the quiz render API + browser host (Claude Code host model)."""
```

`src/cognit/mcp/__main__.py`:

```python
from cognit.mcp.server import main

if __name__ == "__main__":
    main()
```

`tests/mcp/__init__.py`: (empty file)

- [ ] **Step 3: Write the failing import test**

`tests/mcp/test_import.py`:

```python
def test_mcp_package_imports():
    import cognit.mcp  # noqa: F401


def test_fastmcp_available():
    from mcp.server.fastmcp import FastMCP  # noqa: F401
```

- [ ] **Step 4: Run it — expect failure (server.main not yet defined blocks __main__, but these two tests should pass once the package exists)**

Run: `uv run pytest tests/mcp/test_import.py -v`
Expected: PASS (the package + `mcp` import resolve). If `mcp` is not installed, run `uv sync` first.

- [ ] **Step 5: Sync the new dependency**

Run: `uv sync`
Expected: resolves and installs `mcp`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/cognit/mcp/__init__.py src/cognit/mcp/__main__.py tests/mcp/__init__.py tests/mcp/test_import.py
git commit -m "feat(mcp): scaffold cognit.mcp package + declare mcp dependency"
```

---

## Task 2: `QuizState` — in-memory state + write-through snapshot

**Files:**
- Create: `src/cognit/mcp/state.py`
- Test: `tests/mcp/test_state.py`

- [ ] **Step 1: Write the failing tests**

`tests/mcp/test_state.py`:

```python
import json
from pathlib import Path

from cognit.engine.models import MCQQuestion, Quiz
from cognit.mcp.state import QuizState


def _quiz() -> Quiz:
    return Quiz(
        pr_number=7,
        questions=[MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A")],
    )


def test_set_quiz_persists_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "snap.json"
    s = QuizState(pr_number=7, snapshot_path=snap)
    s.set_quiz(_quiz())
    assert s.quiz is not None and s.quiz.questions[0].id == "q1"
    data = json.loads(snap.read_text())
    assert data["quiz"]["questions"][0]["id"] == "q1"


def test_record_answer(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    s.set_quiz(_quiz())
    s.record_answer("q1", "A")
    assert s.answers == {"q1": "A"}


def test_loads_existing_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "s.json"
    QuizState(pr_number=7, snapshot_path=snap).set_quiz(_quiz())
    # A fresh instance over the same path rehydrates.
    s2 = QuizState(pr_number=7, snapshot_path=snap)
    assert s2.quiz is not None and s2.quiz.questions[0].id == "q1"
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/mcp/test_state.py -v`
Expected: FAIL (`ModuleNotFoundError: cognit.mcp.state`).

- [ ] **Step 3: Implement `QuizState`**

`src/cognit/mcp/state.py`:

```python
"""Authoritative session state for the MCP server: the quiz, the browser-collected
answers, and the last grading result. Write-through to a snapshot file on every
mutation so a crash/exit loses nothing and a fresh process rehydrates."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from cognit.engine.models import Quiz, Results


class QuizState:
    def __init__(self, *, pr_number: int, snapshot_path: Path) -> None:
        self.pr_number = pr_number
        self._snapshot_path = snapshot_path
        self._lock = threading.Lock()
        self.quiz: Quiz | None = None
        self.answers: dict[str, str] = {}
        self.results: Results | None = None
        self._load()

    def _load(self) -> None:
        if not self._snapshot_path.exists():
            return
        try:
            data = json.loads(self._snapshot_path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if data.get("quiz"):
            self.quiz = Quiz.model_validate(data["quiz"])
        self.answers = dict(data.get("answers") or {})
        if data.get("results"):
            self.results = Results.model_validate(data["results"])

    def _persist(self) -> None:
        payload = {
            "quiz": self.quiz.model_dump() if self.quiz else None,
            "answers": self.answers,
            "results": self.results.model_dump() if self.results else None,
        }
        self._snapshot_path.write_text(json.dumps(payload))

    def set_quiz(self, quiz: Quiz) -> None:
        with self._lock:
            self.quiz = quiz
            self.answers = {}
            self.results = None
            self._persist()

    def replace_question(self, index: int, question: object) -> None:
        with self._lock:
            if self.quiz is None or not (0 <= index < len(self.quiz.questions)):
                raise IndexError(index)
            qs = list(self.quiz.questions)
            qs[index] = question  # type: ignore[assignment]
            self.quiz = self.quiz.model_copy(update={"questions": qs})
            self.answers.pop(self.quiz.questions[index].id, None)
            self.results = None
            self._persist()

    def record_answer(self, question_id: str, value: str) -> None:
        with self._lock:
            self.answers[question_id] = value
            self._persist()

    def set_results(self, results: Results) -> None:
        with self._lock:
            self.results = results
            self._persist()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "quiz": self.quiz.model_dump() if self.quiz else None,
                "answers": dict(self.answers),
                "results": self.results.model_dump() if self.results else None,
            }
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/mcp/test_state.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cognit/mcp/state.py tests/mcp/test_state.py
git commit -m "feat(mcp): QuizState with write-through snapshot"
```

---

## Task 3: `validate_and_prepare` — port the submit-validation + answer shuffle

**Files:**
- Create: `src/cognit/mcp/validate.py`
- Test: `tests/mcp/test_validate.py`

This ports the logic in `llm_claude_agent.py:_submit_validation_hook` (148-223) into a pure function and reuses `engine.generate._neutralize_mermaid_labels` for the shuffle.

- [ ] **Step 1: Write the failing tests**

`tests/mcp/test_validate.py`:

```python
from cognit.mcp.validate import validate_and_prepare


def _mcq(qid="q1"):
    return {"type": "mcq", "id": qid, "prompt": "p", "options": ["A", "B"],
            "answer": "A", "explanation": "because A"}


def _good_mermaid():
    # 4 uniform, distinct, valid diagrams; answer key present.
    return {"type": "mermaid", "id": "m1", "prompt": "which flow?",
            "options": {
                "A": "flowchart LR; A[req]-->B[auth]-->C[route]",
                "B": "flowchart LR; A[req]-->B[route]-->C[auth]",
                "C": "flowchart LR; A[req]-->B[auth]-->C[cache]",
                "D": "flowchart LR; A[req]-->B[cache]-->C[auth]",
            },
            "answer": "A", "explanation": "auth precedes routing"}


def test_valid_quiz_returns_quiz_no_failures():
    quiz, failures = validate_and_prepare({"version": "1", "questions": [_mcq()]}, pr_number=7)
    assert failures == []
    assert quiz is not None and quiz.pr_number == 7


def test_mermaid_wrong_option_count_fails():
    m = _good_mermaid()
    m["options"].pop("D")  # only 3
    quiz, failures = validate_and_prepare({"version": "1", "questions": [m]}, pr_number=7)
    assert quiz is None
    assert any("exactly 4 options" in f for f in failures)


def test_missing_explanation_fails():
    m = _mcq()
    m["explanation"] = ""
    quiz, failures = validate_and_prepare({"version": "1", "questions": [m]}, pr_number=7)
    assert quiz is None
    assert any("explanation" in f for f in failures)


def test_malformed_shape_fails():
    quiz, failures = validate_and_prepare({"version": "1", "questions": [{"type": "mcq"}]}, pr_number=7)
    assert quiz is None
    assert any("malformed" in f for f in failures)


def test_valid_mermaid_answer_survives_shuffle():
    # After the position shuffle, the answer key must still point at the originally-correct diagram.
    q, failures = validate_and_prepare({"version": "1", "questions": [_good_mermaid()]}, pr_number=7)
    assert failures == []
    mq = q.questions[0]
    assert mq.answer in mq.options  # key is valid post-shuffle (model_validator enforces this too)
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/mcp/test_validate.py -v`
Expected: FAIL (`ModuleNotFoundError: cognit.mcp.validate`).

- [ ] **Step 3: Implement `validate_and_prepare`**

`src/cognit/mcp/validate.py`:

```python
"""Validate an agent-submitted quiz draft and prepare it for rendering.

Pure port of the SDK submit-validation hook (llm_claude_agent.py:_submit_validation_hook):
Pydantic shape, then per-mermaid checks (exactly 4 options, answer in keys, each diagram
parses, the four are uniform AND distinct), then a missing-`explanation` check. On success
wraps into a Quiz(pr_number=...) and runs the answer-position shuffle
(engine.generate._neutralize_mermaid_labels) — load-bearing anti-leak, see that function.

Returns (Quiz, []) on success or (None, [reasons]) — the reasons are handed back to the
agent so it self-corrects, exactly as the SDK hook's deny reason did.
"""

from __future__ import annotations

from pydantic import ValidationError

from cognit.engine.generate import _neutralize_mermaid_labels
from cognit.engine.mermaid import distinctness_failure, is_valid_mermaid, uniformity_failures
from cognit.engine.models import (
    MCQQuestion,
    MermaidQuestion,
    Quiz,
    QuizDraft,
    TrueFalseQuestion,
)


def validate_and_prepare(draft: dict, *, pr_number: int) -> tuple[Quiz | None, list[str]]:
    try:
        parsed = QuizDraft.model_validate(draft)
    except ValidationError as e:
        return None, [f"the submitted quiz is malformed: {e.errors()}"]

    failures: list[str] = []
    for q in parsed.questions:
        if (
            isinstance(q, (MCQQuestion, TrueFalseQuestion, MermaidQuestion))
            and not q.explanation.strip()
        ):
            failures.append(
                f"question {q.id!r}: missing a one-sentence `explanation` "
                "(shown to the reader after they answer)"
            )
        if not isinstance(q, MermaidQuestion):
            continue
        if len(q.options) != 4:
            failures.append(
                f"question {q.id!r}: must have exactly 4 options, has {len(q.options)}"
            )
            continue
        if q.answer not in q.options:
            failures.append(f"question {q.id!r}: answer {q.answer!r} is not one of the option keys")
        for label, src in q.options.items():
            if not is_valid_mermaid(src, strict=False):
                failures.append(f"question {q.id!r} option {label}: invalid mermaid syntax")
        failures.extend(f"question {q.id!r}: {m}" for m in uniformity_failures(q.options))
        failures.extend(f"question {q.id!r}: {m}" for m in distinctness_failure(q.options))

    if failures:
        return None, failures

    quiz = Quiz(version="1", pr_number=pr_number, questions=parsed.questions)
    return _neutralize_mermaid_labels(quiz), []
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/mcp/test_validate.py -v`
Expected: PASS (5 tests). (Note: `is_valid_mermaid` falls back to the Python regex gate when neither `mmdc` nor `docker` is present — the `_good_mermaid` fixtures pass that gate.)

- [ ] **Step 5: Commit**

```bash
git add src/cognit/mcp/validate.py tests/mcp/test_validate.py
git commit -m "feat(mcp): validate_and_prepare (submit-validation + answer shuffle, pure)"
```

---

## Task 4: `grade_state` — grade the current answers

**Files:**
- Create: `src/cognit/mcp/grading.py`
- Test: `tests/mcp/test_grading.py`

- [ ] **Step 1: Write the failing test**

`tests/mcp/test_grading.py`:

```python
from pathlib import Path

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, OpenQuestion, Quiz
from cognit.mcp.grading import grade_state
from cognit.mcp.state import QuizState


def test_grade_state_scores_and_stores(tmp_path: Path) -> None:
    quiz = Quiz(
        pr_number=7,
        questions=[
            MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A", explanation="x"),
            OpenQuestion(id="q2", prompt="why?", rubric="mentions X"),
        ],
    )
    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    state.set_quiz(quiz)
    state.record_answer("q1", "A")  # correct
    state.record_answer("q2", "some prose")
    results = grade_state(state, llm=FakeLLM(canned_open_score=80, canned_open_feedback="ok"))
    assert results.per_question[0].correct is True
    assert results.per_question[1].score == 80
    # stored back on the state (so /state can serve it)
    assert state.results is not None and state.results.total_score == results.total_score
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/mcp/test_grading.py -v`
Expected: FAIL (`ModuleNotFoundError: cognit.mcp.grading`).

- [ ] **Step 3: Implement `grade_state`**

`src/cognit/mcp/grading.py`:

```python
"""Grade the answers currently held in QuizState. Reuses engine.grade.grade (deterministic
for mcq/tf/mermaid; the existing strict single-shot grade_open for open questions), so
calibration is identical to today. The agent triggers this but supplies no judgments."""

from __future__ import annotations

from cognit.engine.grade import grade
from cognit.engine.llm import LLMClient
from cognit.engine.models import AnswerEntry, Answers, Results
from cognit.mcp.state import QuizState


def grade_state(state: QuizState, *, llm: LLMClient) -> Results:
    if state.quiz is None:
        raise RuntimeError("no quiz to grade")
    answers = Answers(
        pr_number=state.pr_number,
        entries=[AnswerEntry(question_id=qid, value=val) for qid, val in state.answers.items()],
    )
    results = grade(state.quiz, answers, llm=llm)
    state.set_results(results)
    return results
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/mcp/test_grading.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cognit/mcp/grading.py tests/mcp/test_grading.py
git commit -m "feat(mcp): grade_state reusing engine.grade (handler-owned grading)"
```

---

## Task 5: `build_web_app` — the browser projection over QuizState

**Files:**
- Create: `src/cognit/mcp/web.py`
- Test: `tests/mcp/test_web.py`

- [ ] **Step 1: Write the failing tests (httpx live server, mirroring tests/conftest.py)**

`tests/mcp/test_web.py`:

```python
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from cognit.engine.models import MCQQuestion, Quiz
from cognit.mcp.state import QuizState
from cognit.mcp.web import build_web_app


def _free_port() -> int:
    s = socket.socket(); s.bind(("", 0)); p = s.getsockname()[1]; s.close(); return p


def _serve(app: FastAPI, port: int):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True); t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/state", timeout=0.3).status_code == 200:
                return server, t
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture
def client(tmp_path: Path):
    state = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    state.set_quiz(Quiz(pr_number=7, questions=[
        MCQQuestion(id="q1", prompt="p", options=["A", "B"], answer="A", explanation="x")]))
    posted: list[str] = []
    app = build_web_app(state, pr_url="https://x/pull/7", post_comment=lambda b: (posted.append(b), "http://c/1")[1])
    port = _free_port()
    server, t = _serve(app, port)
    try:
        yield httpx.Client(base_url=f"http://127.0.0.1:{port}"), state, posted
    finally:
        server.should_exit = True; t.join(timeout=2)


def test_state_serves_quiz(client):
    c, _state, _ = client
    body = c.get("/state").json()
    assert body["quiz"]["questions"][0]["id"] == "q1"
    assert body["answers"] == {}


def test_post_answer_records(client):
    c, state, _ = client
    assert c.post("/answer", json={"question_id": "q1", "value": "A"}).status_code == 200
    assert state.answers == {"q1": "A"}


def test_publish_calls_post_comment(client):
    c, state, posted = client
    from cognit.engine.models import Results, QuestionResult
    state.set_results(Results(pr_number=7, total_score=100,
                              per_question=[QuestionResult(question_id="q1", correct=True, score=100, feedback="")]))
    r = c.post("/publish")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(posted) == 1


def test_index_serves_page(client):
    c, _state, _ = client
    html = c.get("/").text
    assert "<html" in html.lower() or "<!doctype" in html.lower()
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/mcp/test_web.py -v`
Expected: FAIL (`ModuleNotFoundError: cognit.mcp.web`).

- [ ] **Step 3: Implement `build_web_app`**

`src/cognit/mcp/web.py`:

```python
"""FastAPI app: the browser projection over QuizState.

Endpoints:
  GET  /state    — JSON {quiz, answers, results}; the browser polls this
  POST /answer   — {question_id, value} → record a browser-side answer
  POST /publish  — human-gated: render + post the results scorecard comment (reuses
                   ghio.pr.post_comment). The ONLY outward-facing action; never an agent tool.
  GET  /         — the quiz page (polls /state)
  GET  /static/* — bundled assets
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cognit.comment.render import render_results_inlined
from cognit.engine.models import AnswerEntry, Answers
from cognit.mcp.state import QuizState

_ASSETS_DIR = Path(__file__).parent / "assets"


def build_web_app(
    state: QuizState,
    *,
    pr_url: str,
    post_comment: Callable[[str], str],
) -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(_ASSETS_DIR)), name="static")
    index_html = (_ASSETS_DIR / "index.html").read_text()

    @app.get("/state")
    def get_state() -> JSONResponse:
        return JSONResponse(state.snapshot())

    @app.post("/answer")
    async def post_answer(req: Request) -> JSONResponse:
        body = await req.json()
        state.record_answer(str(body["question_id"]), str(body["value"]))
        return JSONResponse({"ok": True})

    @app.post("/publish")
    def publish() -> JSONResponse:
        if state.quiz is None or state.results is None:
            return JSONResponse({"ok": False, "error": "nothing graded to publish"}, status_code=409)
        answers = Answers(
            pr_number=state.pr_number,
            entries=[AnswerEntry(question_id=q, value=v) for q, v in state.answers.items()],
        )
        url = post_comment(render_results_inlined(state.quiz, answers, state.results))
        return JSONResponse({"ok": True, "total_score": state.results.total_score, "comment_url": url})

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(index_html)

    return app
```

- [ ] **Step 4: Create the minimal assets so the page + StaticFiles resolve**

`src/cognit/mcp/assets/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>cognit</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main id="root">Loading…</main>
  <script src="/static/quiz_mcp.js"></script>
</body>
</html>
```

`src/cognit/mcp/assets/styles.css`:

```css
body { font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #1f2328; }
.q { border: 1px solid #d0d7de; border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
.q h3 { margin: 0 0 8px; font-size: 13px; color: #656d76; }
.opt { display: block; margin: 4px 0; cursor: pointer; }
.opt.sel { font-weight: 700; }
.result { font-weight: 700; }
```

`src/cognit/mcp/assets/quiz_mcp.js` (functional M1 renderer — polished `server/assets/quiz.js` port is deferred):

```javascript
// cognit MCP browser: polls /state, renders questions, posts answers, shows results.
const root = document.getElementById("root");
let answers = {};

async function postAnswer(qid, value) {
  answers[qid] = value;
  await fetch("/answer", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ question_id: qid, value }) });
  render();
}

function renderQuestion(q, i) {
  const d = document.createElement("div"); d.className = "q";
  const h = document.createElement("h3"); h.textContent = `Question ${i + 1} · ${q.type}`; d.appendChild(h);
  const p = document.createElement("p"); p.textContent = q.prompt; d.appendChild(p);
  if (q.type === "mcq") {
    q.options.forEach((opt) => {
      const el = document.createElement("label"); el.className = "opt" + (answers[q.id] === opt ? " sel" : "");
      el.textContent = opt; el.onclick = () => postAnswer(q.id, opt); d.appendChild(el);
    });
  } else if (q.type === "tf") {
    ["true", "false"].forEach((v) => {
      const el = document.createElement("label"); el.className = "opt" + (answers[q.id] === v ? " sel" : "");
      el.textContent = v; el.onclick = () => postAnswer(q.id, v); d.appendChild(el);
    });
  } else if (q.type === "mermaid") {
    Object.keys(q.options).forEach((label) => {
      const el = document.createElement("label"); el.className = "opt" + (answers[q.id] === label ? " sel" : "");
      el.textContent = `diagram ${label}`; el.onclick = () => postAnswer(q.id, label); d.appendChild(el);
    });
  } else if (q.type === "open") {
    const ta = document.createElement("textarea"); ta.value = answers[q.id] || "";
    ta.oninput = (e) => { answers[q.id] = e.target.value; };
    ta.onblur = () => postAnswer(q.id, ta.value); d.appendChild(ta);
  }
  return d;
}

let state = null;
async function tick() {
  state = await (await fetch("/state")).json();
  answers = state.answers || {};
  render();
}

function render() {
  if (!state || !state.quiz) { root.textContent = "Waiting for the agent…"; return; }
  root.innerHTML = "";
  state.quiz.questions.forEach((q, i) => root.appendChild(renderQuestion(q, i)));
  if (state.results) {
    const r = document.createElement("div"); r.className = "result";
    r.textContent = `Score: ${state.results.total_score} / 100`;
    root.appendChild(r);
  }
}

setInterval(tick, 1000);
tick();
```

- [ ] **Step 5: Run — expect pass**

Run: `uv run pytest tests/mcp/test_web.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cognit/mcp/web.py src/cognit/mcp/assets/ tests/mcp/test_web.py
git commit -m "feat(mcp): browser projection (FastAPI over QuizState) + functional renderer"
```

---

## Task 6: FastMCP tools + `main()` wiring

**Files:**
- Create: `src/cognit/mcp/server.py`
- Test: `tests/mcp/test_tools.py`

The tool *handlers* are thin wrappers over the pure functions; tests call the wrappers directly (no `claude` subprocess). `main()` is the process wiring (not unit-tested here; exercised in M2 end-to-end).

- [ ] **Step 1: Write the failing tests**

`tests/mcp/test_tools.py`:

```python
from pathlib import Path

from cognit.engine.llm_fake import FakeLLM
from cognit.mcp import server as srv
from cognit.mcp.state import QuizState


def _draft():
    return {"version": "1", "questions": [
        {"type": "mcq", "id": "q1", "prompt": "p", "options": ["A", "B"],
         "answer": "A", "explanation": "because A"}]}


def _state(tmp_path: Path) -> QuizState:
    return QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")


def test_set_quiz_renders(tmp_path: Path):
    state = _state(tmp_path)
    out = srv.do_set_quiz(state, _draft())
    assert out["ok"] is True
    assert state.quiz is not None and state.quiz.questions[0].id == "q1"


def test_set_quiz_rejects_with_reasons(tmp_path: Path):
    state = _state(tmp_path)
    bad = {"version": "1", "questions": [{"type": "mcq", "id": "q1", "prompt": "p",
            "options": ["A", "B"], "answer": "A", "explanation": ""}]}
    out = srv.do_set_quiz(state, bad)
    assert out["ok"] is False
    assert any("explanation" in r for r in out["failures"])
    assert state.quiz is None  # rejected → not rendered


def test_replace_question(tmp_path: Path):
    state = _state(tmp_path)
    srv.do_set_quiz(state, _draft())
    new = {"type": "mcq", "id": "q1b", "prompt": "p2", "options": ["X", "Y"],
           "answer": "Y", "explanation": "because Y"}
    out = srv.do_replace_question(state, 0, new)
    assert out["ok"] is True
    assert state.quiz.questions[0].id == "q1b"


def test_grade(tmp_path: Path):
    state = _state(tmp_path)
    srv.do_set_quiz(state, _draft())
    state.record_answer("q1", "A")
    out = srv.do_grade(state, llm=FakeLLM())
    assert out["total_score"] == 100
    assert state.results is not None
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/mcp/test_tools.py -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'do_set_quiz'`).

- [ ] **Step 3: Implement `server.py`**

`src/cognit/mcp/server.py`:

```python
"""The MCP surface (FastMCP stdio tools) + process wiring.

Tools are thin: each `do_*` function holds the pure logic (unit-tested directly); the
`@mcp.tool()` wrappers just adapt args/results. `main()` reads env (port/snapshot/PR),
starts the FastAPI browser host on a daemon thread (fail-hard on bind conflict so a
session never silently attaches to another), then runs the stdio MCP loop.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

from cognit.engine.llm import LLMClient
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import Question
from cognit.mcp.grading import grade_state
from cognit.mcp.state import QuizState
from cognit.mcp.validate import validate_and_prepare
from cognit.mcp.web import build_web_app
from cognit.ghio.pr import post_comment as gh_post_comment

# ── pure tool logic (unit-tested) ────────────────────────────────────────────


def do_set_quiz(state: QuizState, draft: dict) -> dict:
    quiz, failures = validate_and_prepare(draft, pr_number=state.pr_number)
    if quiz is None:
        return {"ok": False, "failures": failures}
    state.set_quiz(quiz)
    return {"ok": True, "rendered": len(quiz.questions)}


def do_replace_question(state: QuizState, index: int, question: dict) -> dict:
    from pydantic import TypeAdapter, ValidationError

    try:
        parsed = TypeAdapter(Question).validate_python(question)
    except ValidationError as e:
        return {"ok": False, "failures": [f"malformed question: {e.errors()}"]}
    try:
        state.replace_question(index, parsed)
    except IndexError:
        n = len(state.quiz.questions) if state.quiz else 0
        return {"ok": False, "failures": [f"index {index} out of range (have {n})"]}
    return {"ok": True}


def do_get_answers(state: QuizState) -> dict:
    snap = state.snapshot()
    return {"answers": snap["answers"], "quiz": snap["quiz"]}


def do_grade(state: QuizState, *, llm: LLMClient) -> dict:
    results = grade_state(state, llm=llm)
    return results.model_dump()


# ── process wiring ───────────────────────────────────────────────────────────


def _build_mcp(state: QuizState, llm: LLMClient) -> FastMCP:
    mcp = FastMCP("cognit")

    @mcp.tool()
    def set_quiz(quiz: dict) -> dict:
        """Render/replace the whole quiz in the browser. `quiz` is {version, questions:[...]}.
        Rejected (with reasons to fix) if any mermaid set is invalid/non-uniform/non-distinct
        or any question lacks an explanation."""
        return do_set_quiz(state, quiz)

    @mcp.tool()
    def replace_question(index: int, question: dict) -> dict:
        """Replace the 0-based question at `index` (skip-and-replace)."""
        return do_replace_question(state, index, question)

    @mcp.tool()
    def get_answers() -> dict:
        """Read back the answers the developer selected in the browser + the current quiz."""
        return do_get_answers(state)

    @mcp.tool()
    def grade() -> dict:
        """Grade the current answers (deterministic + strict open grading) and show the
        scorecard in the browser. Supply no judgments — scoring is computed here."""
        return do_grade(state, llm=llm)

    return mcp


def _start_web(state: QuizState, *, pr_url: str, port: int) -> None:
    app = build_web_app(state, pr_url=pr_url, post_comment=lambda body: gh_post_comment(pr_url, body))
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    uvicorn.Server(cfg).run()


def main() -> None:
    pr_url = os.environ["COGNIT_PR_URL"]
    pr_number = int(os.environ["COGNIT_PR_NUMBER"])
    port = int(os.environ["COGNIT_HTTP_PORT"])
    snapshot = Path(os.environ["COGNIT_SNAPSHOT_PATH"])

    state = QuizState(pr_number=pr_number, snapshot_path=snapshot)
    # Fail hard if the port is taken — never silently serve another session's quiz.
    import socket

    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", port))  # raises OSError if taken → process exits non-zero

    threading.Thread(target=_start_web, args=(state,), kwargs={"pr_url": pr_url, "port": port}, daemon=True).start()
    llm: LLMClient = ClaudeAgentLLM()
    _build_mcp(state, llm).run()  # stdio transport; blocks until claude closes stdin
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/mcp/test_tools.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the whole mcp suite + type-check**

Run: `uv run pytest tests/mcp/ -v && uv run mypy src/cognit/mcp`
Expected: all pass; mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/cognit/mcp/server.py tests/mcp/test_tools.py
git commit -m "feat(mcp): FastMCP render API tools + main() process wiring"
```

---

## Task 7: Manual end-to-end smoke (static fixture, no agent)

Proves the design's M1 acceptance: a quiz renders, is answerable, and is gradable/publishable through the real server — without an agent. No new production code; a throwaway driver script + manual browser check.

**Files:**
- Create: `scripts/m1_smoke.py` (throwaway; not committed)

- [ ] **Step 1: Write a driver that seeds a fixture quiz and serves the web app**

`scripts/m1_smoke.py`:

```python
"""Throwaway M1 smoke: serve the browser host with a static fixture quiz (no agent)."""
import tempfile, webbrowser
from pathlib import Path

import uvicorn

from cognit.engine.models import MCQQuestion, OpenQuestion, Quiz
from cognit.mcp.state import QuizState
from cognit.mcp.web import build_web_app

state = QuizState(pr_number=1, snapshot_path=Path(tempfile.mktemp(suffix=".json")))
state.set_quiz(Quiz(pr_number=1, questions=[
    MCQQuestion(id="q1", prompt="What does `b = a` do for lists?",
                options=["copies", "aliases the same object"], answer="aliases the same object",
                explanation="b and a point at one list."),
    OpenQuestion(id="q2", prompt="Why is that a footgun?", rubric="mutation through one name affects the other"),
]))
app = build_web_app(state, pr_url="https://example/pull/1", post_comment=lambda b: print("PUBLISHED:\n", b) or "http://local/comment")
webbrowser.open("http://127.0.0.1:8800")
uvicorn.run(app, host="127.0.0.1", port=8800, log_level="warning")
```

- [ ] **Step 2: Run it and verify in the browser**

Run: `uv run python scripts/m1_smoke.py`
Then in the browser at `http://127.0.0.1:8800`: the two questions render; clicking an MCQ option marks it; typing in the open box and clicking away records it. Confirm with `curl -s http://127.0.0.1:8800/state` that `answers` reflects your picks. Ctrl-C to stop.

Expected: questions render; `/state` shows recorded answers.

- [ ] **Step 3: Clean up**

```bash
rm scripts/m1_smoke.py
```

(Nothing to commit — this task is a manual gate.)

---

## Self-review

**Spec coverage (against the design doc §4, §8 M1):**
- MCP server as standalone stdio + render API → Tasks 2-6 (`set_quiz`, `replace_question`, `get_answers`, `grade`). ✅
- Browser host + `/state` polling + answer entry → Task 5. ✅
- Human-gated `/publish` (not an agent tool) → Task 5 (`POST /publish`, browser-driven). ✅
- Handler-owned grading reusing `grade_open` → Task 4 (`grade_state` → `engine.grade.grade`). ✅
- Mermaid validation + answer shuffle moved into the render path → Task 3. ✅
- Authoritative state + write-through snapshot → Task 2. ✅
- Per-session port (fail-hard) + per-PR snapshot via env → Task 6 `main()`. ✅
- Static-fixture end-to-end (no agent) → Task 7. ✅
- *Out of M1 scope (M2/M3, by design §8):* the launcher, the confined `claude` session + confinement hook, `file_diff` (generation-only), steering instructions, the polished `quiz.js` port, the headless `claude -p` integration test. Noted, not gaps.

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✅

**Type consistency:** `QuizState` methods (`set_quiz`, `replace_question`, `record_answer`, `set_results`, `snapshot`) are used consistently across Tasks 4-6. `validate_and_prepare(draft, *, pr_number) -> (Quiz|None, list[str])` matches its callers in Task 6. `do_set_quiz/do_replace_question/do_get_answers/do_grade` signatures match the Task 6 tests. `grade_state(state, *, llm)` matches Task 4. ✅

**One known environment dependency:** `is_valid_mermaid` uses `mmdc`/`docker` when present, else a Python regex gate; the Task 3 fixtures are written to pass the regex gate, so the suite is deterministic on a bare machine.
