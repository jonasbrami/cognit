# cognit steering polish (Milestone 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make the steerable flow correct and presentable: `replace_question` enforces the same quality bar as `set_quiz`, grading reads a lock-coherent snapshot, and the browser renders mermaid diagrams (not just labels).

**Architecture:** Small, focused changes on the M1/M2 `cognit.mcp` package + the browser asset. Builds on a generation flow already validated end-to-end (M2-6).

**Scope note:** Retiring the OLD generation path + its tests is a SEPARATE cleanup phase after this. v2 (plugin packaging, `/cognit:take`) remains deferred.

---

## Task M3-1: `replace_question` validation parity

Factor the per-question validation out of `validate_and_prepare` and reuse it so `replace_question` enforces the same invariants (non-empty `explanation`; for mermaid: 4 options, answer-in-keys, each diagram valid, uniform, distinct).

**Files:** Modify `src/cognit/mcp/validate.py`, `src/cognit/mcp/server.py`; Test `tests/mcp/test_validate.py`, `tests/mcp/test_tools.py`.

- [ ] **Step 1 — failing tests.** Add to `tests/mcp/test_validate.py`:
```python
from cognit.mcp.validate import validate_question


def test_validate_question_flags_blank_explanation():
    fails = validate_question({"type": "mcq", "id": "q", "prompt": "p",
                               "options": ["A", "B"], "answer": "A", "explanation": ""})
    assert any("explanation" in f for f in fails)


def test_validate_question_ok_for_good_mcq():
    assert validate_question({"type": "mcq", "id": "q", "prompt": "p",
                              "options": ["A", "B"], "answer": "A", "explanation": "why"}) == []


def test_validate_question_rejects_bad_mermaid_count():
    q = {"type": "mermaid", "id": "m", "prompt": "p", "answer": "A", "explanation": "e",
         "options": {"A": "flowchart LR\nA-->B", "B": "flowchart LR\nA-->C"}}
    assert any("exactly 4 options" in f for f in validate_question(q))
```
Add to `tests/mcp/test_tools.py`:
```python
def test_replace_question_rejects_blank_explanation(tmp_path: Path):
    state = _state(tmp_path)
    srv.do_set_quiz(state, _draft())
    bad = {"type": "mcq", "id": "q1c", "prompt": "p", "options": ["A", "B"],
           "answer": "A", "explanation": ""}
    out = srv.do_replace_question(state, 0, bad)
    assert out["ok"] is False and any("explanation" in r for r in out["failures"])
    assert state.quiz.questions[0].id == "q1"  # unchanged on rejection
```

- [ ] **Step 2 — run, expect FAIL.** `uv run pytest tests/mcp/test_validate.py tests/mcp/test_tools.py -v`

- [ ] **Step 3 — implement.** In `src/cognit/mcp/validate.py`, extract a pure `validate_question(q_dict) -> list[str]` that validates ONE question dict: parse it via `TypeAdapter(Question)` (return `[f"malformed question: {e.errors()}"]` on `ValidationError`); then the explanation check + (for `MermaidQuestion`) the count/answer-key/`is_valid_mermaid`/`uniformity_failures`/`distinctness_failure` checks — the SAME logic currently inlined in `validate_and_prepare`'s loop. Refactor `validate_and_prepare` to call `validate_question` per question (preserving its existing return shape and the shuffle). Add `from pydantic import TypeAdapter` and `from cognit.engine.models import Question` as needed.

  In `src/cognit/mcp/server.py`, change `do_replace_question` to validate before mutating:
```python
def do_replace_question(state: QuizState, index: int, question: dict[str, Any]) -> dict[str, Any]:
    failures = validate_question(question)
    if failures:
        return {"ok": False, "failures": failures}
    from pydantic import TypeAdapter
    parsed = TypeAdapter(Question).validate_python(question)
    try:
        state.replace_question(index, parsed)
    except IndexError:
        n = len(state.quiz.questions) if state.quiz else 0
        return {"ok": False, "failures": [f"index {index} out of range (have {n})"]}
    return {"ok": True}
```
  (Import `validate_question` from `cognit.mcp.validate`. Note: a single replaced mermaid question can't be checked for 4-way uniformity against siblings — `validate_question` checks only that question's own 4 options, which is the right per-question bar.)

- [ ] **Step 4 — run, expect PASS** (new + existing). `uv run pytest tests/mcp/ tests/engine/test_prompts.py -v`
- [ ] **Step 5 — verify** `uv run pytest -q` green, `uv run mypy` 0 errors, `uv run ruff check src/cognit/mcp/validate.py src/cognit/mcp/server.py`.
- [ ] **Step 6 — commit.** `git commit -m "feat(mcp): replace_question enforces set_quiz's validation bar" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"`

---

## Task M3-2: lock-coherent grading snapshot

`grade_state` reads `state.quiz`/`state.answers` outside the lock — a concurrent `set_quiz`/`record_answer` could iterate a changing dict. Capture both atomically.

**Files:** Modify `src/cognit/mcp/state.py`, `src/cognit/mcp/grading.py`; Test `tests/mcp/test_state.py`.

- [ ] **Step 1 — failing test.** Add to `tests/mcp/test_state.py`:
```python
def test_snapshot_for_grading_atomic(tmp_path: Path) -> None:
    s = QuizState(pr_number=7, snapshot_path=tmp_path / "s.json")
    assert s.snapshot_for_grading() is None  # no quiz
    s.set_quiz(_quiz())
    s.record_answer("q1", "A")
    snap = s.snapshot_for_grading()
    assert snap is not None
    quiz, answers = snap
    assert quiz.questions[0].id == "q1" and answers == {"q1": "A"}
    answers["q1"] = "MUTATED"  # caller's copy must not affect state
    assert s.answers == {"q1": "A"}
```

- [ ] **Step 2 — run, expect FAIL.** `uv run pytest tests/mcp/test_state.py -v`

- [ ] **Step 3 — implement.** In `QuizState` add:
```python
    def snapshot_for_grading(self) -> "tuple[Quiz, dict[str, str]] | None":
        """Atomically capture (quiz, answers-copy) under the lock for grading."""
        with self._lock:
            if self.quiz is None:
                return None
            return self.quiz, dict(self.answers)
```
  In `grading.py`, change `grade_state` to use it:
```python
def grade_state(state: QuizState, *, llm: LLMClient) -> Results:
    snap = state.snapshot_for_grading()
    if snap is None:
        raise RuntimeError("no quiz to grade")
    quiz, answers_map = snap
    answers = Answers(
        pr_number=state.pr_number,
        entries=[AnswerEntry(question_id=qid, value=val) for qid, val in answers_map.items()],
    )
    results = grade(quiz, answers, llm=llm)
    state.set_results(results)
    return results
```

- [ ] **Step 4 — run, expect PASS.** `uv run pytest tests/mcp/test_state.py tests/mcp/test_grading.py -v`
- [ ] **Step 5 — verify** `uv run pytest -q` green, `uv run mypy` 0 errors, `uv run ruff check src/cognit/mcp/state.py src/cognit/mcp/grading.py`.
- [ ] **Step 6 — commit.** `git commit -m "fix(mcp): grade from a lock-coherent quiz+answers snapshot" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"`

---

## Task M3-3: render mermaid diagrams in the browser

The M1 `quiz_mcp.js` shows mermaid options as text labels (`"diagram A"`). Render the actual diagrams with the bundled `mermaid` UMD, like `server/assets/quiz.js` does.

**Files:** Add `src/cognit/mcp/assets/mermaid.min.js` (copy of the existing bundle); Modify `src/cognit/mcp/assets/index.html`, `src/cognit/mcp/assets/quiz_mcp.js`. (No Python; verify by serving.)

- [ ] **Step 1 — copy the bundled UMD.** `cp src/cognit/server/assets/mermaid.min.js src/cognit/mcp/assets/mermaid.min.js`.

- [ ] **Step 2 — load it + initialize in `index.html`.** Add before `quiz_mcp.js`:
```html
  <script src="/static/mermaid.min.js"></script>
```
And in `quiz_mcp.js`, after the `const root = ...` line, initialize once (mirror `server/assets/quiz.js`'s init, `startOnLoad:false`, `securityLevel:"strict"`):
```javascript
if (window.mermaid) window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
```

- [ ] **Step 3 — render diagrams for mermaid questions.** In `renderQuestion`'s `mermaid` branch, instead of a plain label per option, render each option's diagram source into a `.mermaid`-classed node (using `textContent` — never `innerHTML` — for the source) inside a clickable container that still calls `postAnswer(q.id, label)` and reflects selection. After building the question DOM in `renderQuiz`, call `window.mermaid.run({ querySelector: "#root .mermaid" })` (await/catch). Keep the option clickable + the `.sel` selection highlight working (use a wrapper element with `dataset.qid`/`dataset.val` for the selection refresh, with the rendered SVG inside). Keep the label (`diagram A`) as a caption so selection is unambiguous.

- [ ] **Step 4 — verify by serving** (manual, throwaway): write a tiny script that serves `build_web_app` over a `QuizState` seeded with a mermaid question (4 valid `flowchart LR` options), open the browser / `curl /` to confirm the page loads `mermaid.min.js` and `/static/mermaid.min.js` returns 200. Confirm `curl -s http://127.0.0.1:<port>/static/mermaid.min.js | head -c 50` is non-empty JS. Remove the script after. (Full visual confirmation is the user's interactive QA.)

- [ ] **Step 5 — verify** `uv run pytest -q` green (the web tests still pass — `/static` now also serves mermaid.min.js), `uv run ruff check` (assets aren't linted).
- [ ] **Step 6 — commit.** `git add src/cognit/mcp/assets/ && git commit -m "feat(mcp): render mermaid diagrams in the browser quiz" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"`

---

## Self-review
- `replace_question` parity (M3-1) ✅; lock-coherent grading (M3-2) ✅; browser mermaid (M3-3) ✅ — the three carry-forwards from the M1/M2 reviews.
- Type consistency: `validate_question(dict) -> list[str]` reused by `validate_and_prepare` + `do_replace_question`; `snapshot_for_grading() -> tuple[Quiz, dict]|None` consumed by `grade_state`.
- Deferred (recorded): old-path retirement + tests (cleanup phase); full visual mermaid confirmation (user's interactive QA).
