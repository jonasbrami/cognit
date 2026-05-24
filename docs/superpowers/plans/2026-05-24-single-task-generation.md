# Single-Task Quiz Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse two-stage quiz generation (agentic outline → ThreadPoolExecutor mermaid fan-out) into a single agent that submits a complete quiz with rendered mermaid, guarded by a `PreToolUse` validation hook that drives in-turn self-correction.

**Architecture:** One `query()` call. The agent reads the PR (`pr_diff` + repo-confined `Read`/`Grep`/`Glob`) and submits the whole quiz via one MCP tool. A `PreToolUse` hook validates Pydantic shape + mermaid syntax + visual uniformity; on failure it `deny`s with a reason and the agent fixes it within the same turn. No second stage, no thread pool, no `MermaidSpec`/`MermaidPlaceholder`/`MermaidSet`. Grading is a **separate plan** and untouched here.

**Tech Stack:** Python 3.12, `claude-agent-sdk` 0.2.85, Pydantic v2, pytest, `uv`, ruff, mypy. Mermaid validated via `cognit.engine.mermaid.is_valid_mermaid` (uses `mmdc`).

**Spec:** `docs/superpowers/specs/2026-05-24-single-task-quiz-generation-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cognit/engine/mermaid.py` | mermaid validity + **uniformity heuristics** | add `uniformity_failures` + helpers |
| `src/cognit/engine/models.py` | Pydantic models | add `QuizDraft`; delete `QuizOutline`/`MermaidSpec`/`MermaidPlaceholder`/`OutlineQuestion`/`MermaidSet` |
| `src/cognit/engine/llm.py` | `LLMClient` Protocol | `generate_quiz_outline`→`draft_quiz`; drop `generate_mermaid_set` |
| `src/cognit/engine/llm_claude_agent.py` | SDK adapter | add `_submit_validation_hook`; `generate_quiz_outline`→`draft_quiz` (wires hook); delete `generate_mermaid_set`/`_format_misconceptions` |
| `src/cognit/engine/llm_fake.py` | test double | `canned_outline`→`canned_draft`; `draft_quiz`; drop `generate_mermaid_set` |
| `src/cognit/engine/generate.py` | orchestrator | delete stage 2; thin wrapper around `draft_quiz` + shuffle |
| `src/cognit/engine/prompts/` | agent prompts | merge `system_mermaid.txt`→`system_generate.txt`; delete `system_mermaid.txt`+`mermaid.txt` |
| `src/cognit/server/assets/quiz.js` | activity-feed labels | update `TOOL_LABELS` |

Tests live beside their targets under `tests/`. Run everything with `uv run pytest -q`.

---

## Task 1: Mermaid uniformity heuristics

A pure, additive helper — the hook (Task 2) uses it. The anti-leak rule (`system_mermaid.txt` rule 1) says the correct diagram must not look different/bigger than the distractors; this gives the hook a mechanical proxy for that.

**Files:**
- Modify: `src/cognit/engine/mermaid.py`
- Test: `tests/engine/test_mermaid_uniformity.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_mermaid_uniformity.py`:

```python
from cognit.engine.mermaid import uniformity_failures


def _opts(*srcs: str) -> dict[str, str]:
    return {k: s for k, s in zip("ABCD", srcs)}


def test_uniform_diagrams_pass() -> None:
    a = "flowchart LR\n  A-->B\n  B-->C"
    b = "flowchart LR\n  A-->C\n  C-->B"
    c = "flowchart LR\n  B-->A\n  A-->C"
    d = "flowchart LR\n  C-->B\n  B-->A"
    assert uniformity_failures(_opts(a, b, c, d)) == []


def test_mixed_header_or_direction_flagged() -> None:
    fails = uniformity_failures(
        _opts(
            "flowchart LR\nA-->B",
            "flowchart TD\nA-->B",  # different direction
            "flowchart LR\nA-->B",
            "flowchart LR\nA-->B",
        )
    )
    assert any("header" in f for f in fails)


def test_size_outlier_flagged() -> None:
    small = "flowchart LR\nA-->B"
    big = "flowchart LR\n" + "\n".join(f"N{i}-->N{i + 1}" for i in range(8))
    fails = uniformity_failures(_opts(small, small, small, big))
    assert any("size" in f.lower() or "line" in f.lower() for f in fails)


def test_under_two_options_is_noop() -> None:
    assert uniformity_failures({"A": "flowchart LR\nA-->B"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_mermaid_uniformity.py -q`
Expected: FAIL — `ImportError: cannot import name 'uniformity_failures'`.

- [ ] **Step 3: Implement the helper**

Append to `src/cognit/engine/mermaid.py` (after `is_valid_mermaid`). The `re` import already exists at the top of the file.

```python
# --- Visual-uniformity heuristics (anti-leak) -------------------------------
# Coarse, deliberately tunable proxies for system_mermaid.txt rule 1: the four
# option diagrams must not be distinguishable by superficial features (a
# different type/direction, or one noticeably bigger). These catch the common
# leaks, not subtle ones — the hook denies + the agent self-corrects in-turn.

_EDGE_OP = re.compile(r"-->|---|-\.->|==>|-->>|->>|->")
_UNIFORMITY_SIZE_TOLERANCE = 2  # max-min non-empty line count
_UNIFORMITY_EDGE_TOLERANCE = 2  # max-min edge-operator count


def _diagram_header(source: str) -> str:
    """First non-empty line, whitespace-normalized — captures both diagram type
    and direction (e.g. 'flowchart LR', 'sequenceDiagram')."""
    for line in source.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            return normalized
    return ""


def _line_count(source: str) -> int:
    return sum(1 for line in source.splitlines() if line.strip())


def _edge_count(source: str) -> int:
    return len(_EDGE_OP.findall(source))


def uniformity_failures(options: dict[str, str]) -> list[str]:
    """Reasons the option diagrams are NOT visually uniform, or [] if they are.

    Heuristic: all share one header line (type + direction); non-empty line
    counts within a tolerance band; edge-operator counts within a band.
    """
    srcs = list(options.values())
    if len(srcs) < 2:
        return []
    fails: list[str] = []
    headers = sorted({_diagram_header(s) for s in srcs})
    if len(headers) > 1:
        fails.append(f"all diagrams must share one header/direction; got {headers}")
    lines = [_line_count(s) for s in srcs]
    if max(lines) - min(lines) > _UNIFORMITY_SIZE_TOLERANCE:
        fails.append(f"diagram sizes differ too much (line counts {lines}); keep them comparable")
    edges = [_edge_count(s) for s in srcs]
    if max(edges) - min(edges) > _UNIFORMITY_EDGE_TOLERANCE:
        fails.append(f"edge counts differ too much {edges}; keep them comparable")
    return fails
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_mermaid_uniformity.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cognit/engine/mermaid.py tests/engine/test_mermaid_uniformity.py
git commit -m "feat(engine): add mermaid uniformity heuristics for anti-leak validation"
```

---

## Task 2: `QuizDraft` model + submit-validation hook

Additive: introduce the single-call submit model and the hook that validates a submission, *without* wiring them into the pipeline yet (Task 3 does that). Both are independently unit-tested.

**Files:**
- Modify: `src/cognit/engine/models.py`
- Modify: `src/cognit/engine/llm_claude_agent.py`
- Test: `tests/engine/test_submit_validation_hook.py` (create)

- [ ] **Step 1: Add the `QuizDraft` model**

In `src/cognit/engine/models.py`, after the `Quiz` class (around line 56), add:

```python
class QuizDraft(BaseModel):
    """What the single generation agent submits: the final question shapes, no
    pr_number (the orchestrator supplies it). Mermaid questions are fully rendered."""

    version: Literal["1"] = "1"
    questions: list[Question]
```

(`Question`, `Literal`, `BaseModel` are already defined/imported in this file. Leave `QuizOutline` and the other old models in place for now — Task 4 deletes them.)

- [ ] **Step 2: Write the failing hook test**

Create `tests/engine/test_submit_validation_hook.py`:

```python
import asyncio
from typing import Any

import pytest

import cognit.engine.llm_claude_agent as llm_mod
from cognit.engine.llm_claude_agent import _TOOL_SUBMIT, _submit_validation_hook

VALID = "flowchart LR\n  A-->B\n  B-->C"


def _run(tool_input: dict[str, Any], on_event: Any = None) -> dict[str, Any]:
    matcher = _submit_validation_hook(on_event)
    hook = matcher.hooks[0]
    payload = {"tool_name": f"mcp__cognit__{_TOOL_SUBMIT}", "tool_input": tool_input}
    return asyncio.run(hook(payload, None, {}))


def _denied(out: dict[str, Any]) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _mermaid_q(options: dict[str, str], answer: str = "A") -> dict[str, Any]:
    return {"type": "mermaid", "id": "q1", "prompt": "which flow?", "options": options, "answer": answer}


def test_matcher_targets_the_submit_tool() -> None:
    assert _submit_validation_hook(None).matcher == "mcp__cognit__submit_quiz"


def test_valid_quiz_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run({"questions": [_mermaid_q({"A": VALID, "B": VALID, "C": VALID, "D": VALID})]})
    assert out == {}


def test_invalid_mermaid_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: src != "BAD")
    out = _run({"questions": [_mermaid_q({"A": "BAD", "B": VALID, "C": VALID, "D": VALID})]})
    assert _denied(out)
    assert "invalid mermaid" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_wrong_option_count_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run({"questions": [_mermaid_q({"A": VALID, "B": VALID, "C": VALID})]})
    assert _denied(out)
    assert "exactly 4" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_shape_invalid_denied() -> None:
    # missing options/answer/prompt -> QuizDraft.model_validate raises -> deny
    out = _run({"questions": [{"type": "mermaid", "id": "q1"}]})
    assert _denied(out)


def test_non_mermaid_quiz_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run({"questions": [{"type": "mcq", "id": "q1", "prompt": "?", "options": ["x", "y"], "answer": "x"}]})
    assert out == {}


def test_emits_validation_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: False)
    events: list[dict[str, Any]] = []
    _run({"questions": [_mermaid_q({"A": "x", "B": "x", "C": "x", "D": "x"})]}, on_event=events.append)
    texts = [e.get("text", "") for e in events]
    assert any("checking" in t for t in texts)
    assert any("fixing" in t for t in texts)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_submit_validation_hook.py -q`
Expected: FAIL — `ImportError: cannot import name '_submit_validation_hook'`.

- [ ] **Step 4: Implement the hook**

In `src/cognit/engine/llm_claude_agent.py`:

(a) Extend the imports. Change the mermaid import — add it near the other `cognit.engine` imports (after line 51):

```python
from cognit.engine.mermaid import is_valid_mermaid, uniformity_failures
```

Add `ValidationError` to the pydantic usage — at the top with the stdlib/3rd-party imports add:

```python
from pydantic import ValidationError
```

Add `QuizDraft` to the models import (line 50 currently `from cognit.engine.models import MermaidSet, MermaidSpec, QuizOutline`) — for now just add `QuizDraft`, `MermaidQuestion`:

```python
from cognit.engine.models import MermaidQuestion, MermaidSet, MermaidSpec, QuizDraft, QuizOutline
```

(b) Add the constant near the other `_TOOL_*` constants (after line 56):

```python
_TOOL_SUBMIT = "submit_quiz"
```

(c) Add the hook factory after `_read_confinement_hook` (after line 133):

```python
def _deny_submit(
    reason: str, on_event: Callable[[dict[str, Any]], None] | None, n: int
) -> dict[str, Any]:
    if on_event is not None:
        on_event({"kind": "text", "text": f"⟳ fixing {n} issue(s)…", "tool": _TOOL_SUBMIT})
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _submit_validation_hook(
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> HookMatcher:
    """PreToolUse hook on the submit tool: validates the whole quiz at submit time
    and denies with a precise reason so the agent self-corrects in-turn.

    Checks, in order: (1) QuizDraft Pydantic shape; (2) per mermaid question —
    exactly 4 options, `answer` in keys; (3) each diagram parses (is_valid_mermaid,
    strict=False); (4) the 4 diagrams are visually uniform (uniformity_failures).
    Emits `checking…` / `⟳ fixing…` to `on_event` so the activity feed shows it.
    """

    async def _hook(
        hook_input: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        if hook_input.get("tool_name") != f"mcp__cognit__{_TOOL_SUBMIT}":
            return {}
        tool_input = hook_input.get("tool_input") or {}
        if on_event is not None:
            on_event({"kind": "text", "text": "checking diagrams…", "tool": _TOOL_SUBMIT})

        try:
            draft = QuizDraft.model_validate(tool_input)
        except ValidationError as e:
            return _deny_submit(f"the submitted quiz is malformed: {e.errors()}", on_event, 1)

        failures: list[str] = []
        for q in draft.questions:
            if not isinstance(q, MermaidQuestion):
                continue
            if len(q.options) != 4:
                failures.append(f"question {q.id!r}: must have exactly 4 options, has {len(q.options)}")
                continue
            if q.answer not in q.options:
                failures.append(f"question {q.id!r}: answer {q.answer!r} is not one of the option keys")
            for label, src in q.options.items():
                if not await asyncio.to_thread(is_valid_mermaid, src, strict=False):
                    failures.append(f"question {q.id!r} option {label}: invalid mermaid syntax")
            failures.extend(f"question {q.id!r}: {m}" for m in uniformity_failures(q.options))

        if failures:
            reason = "Fix these and resubmit the whole quiz:\n- " + "\n- ".join(failures)
            return _deny_submit(reason, on_event, len(failures))
        return {}

    return HookMatcher(matcher=f"mcp__cognit__{_TOOL_SUBMIT}", hooks=[cast(HookCallback, _hook)])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_submit_validation_hook.py -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Run the full suite (nothing else should break — additive only)**

Run: `uv run pytest -q`
Expected: PASS (existing tests unaffected; `QuizDraft` and the hook are unused so far).

- [ ] **Step 7: Commit**

```bash
git add src/cognit/engine/models.py src/cognit/engine/llm_claude_agent.py tests/engine/test_submit_validation_hook.py
git commit -m "feat(engine): add QuizDraft model and submit-validation hook (unwired)"
```

---

## Task 3: Switch the pipeline to single-stage generation

Rewire the adapter, Protocol, fake, and orchestrator to the one-call flow, and update their tests. Old `generate_mermaid_set` and the dead models stay defined-but-unused (Task 4 removes them) so this task stays green.

**Files:**
- Modify: `src/cognit/engine/llm_claude_agent.py` (`generate_quiz_outline`→`draft_quiz`)
- Modify: `src/cognit/engine/llm.py` (Protocol)
- Modify: `src/cognit/engine/llm_fake.py` (`FakeLLM`)
- Modify: `src/cognit/engine/generate.py` (orchestrator)
- Test: `tests/engine/test_llm_claude_agent.py`, `tests/engine/test_generate.py`, `tests/engine/test_llm_fake.py`, `tests/cli/test_take.py`

- [ ] **Step 1: Rewrite the adapter's outline method as `draft_quiz`**

In `src/cognit/engine/llm_claude_agent.py`, replace the whole `generate_quiz_outline` method (lines 274-331) with:

```python
    def draft_quiz(self, req: GenerateRequest) -> QuizDraft:
        """Single-stage (agentic): the agent fetches the diff, reads the working
        tree with read-only tools, renders any mermaid diagrams itself, and submits
        the complete quiz. The submit-validation hook gates the submission and the
        agent self-corrects in-turn on any failure."""
        system = _load_prompt("system_generate.txt")
        user = _load_prompt("generate.txt").format(
            pr_number=req.pr_number,
            branch=req.branch,
            pr_title=req.pr_title,
            pr_body=req.pr_body,
        )
        captured: list[dict[str, Any]] = []

        self._current_tool = _TOOL_SUBMIT
        if self.on_event is not None:
            self.on_event({"kind": "step", "tool": _TOOL_SUBMIT})

        async def pr_diff_handler(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": fetch_pr_diff(req.pr_url)}]}

        async def submit_handler(args: dict[str, Any]) -> dict[str, Any]:
            captured.append(args)
            return {"content": [{"type": "text", "text": "ok"}]}

        pr_diff_tool = tool(
            _TOOL_PR_DIFF,
            "Fetch the PR's unified diff. Vendored/minified/lock/binary files are "
            "already stripped. Call this first to see what changed.",
            {"type": "object", "properties": {}},
        )(pr_diff_handler)
        submit_tool = tool(
            _TOOL_SUBMIT,
            "Submit the complete quiz, mermaid diagrams fully rendered.",
            QuizDraft.model_json_schema(),
        )(submit_handler)
        server = create_sdk_mcp_server(name="cognit", tools=[pr_diff_tool, submit_tool])

        repo_root = _repo_root()
        self._run_agent(
            system=system,
            user=user,
            server=server,
            allowed_tools=[
                *_OUTLINE_BUILTIN_TOOLS,
                f"mcp__cognit__{_TOOL_PR_DIFF}",
                f"mcp__cognit__{_TOOL_SUBMIT}",
            ],
            tools=_OUTLINE_BUILTIN_TOOLS,
            max_turns=_OUTLINE_MAX_TURNS,
            cwd=repo_root,
            handler=submit_handler,
            hooks={
                "PreToolUse": [
                    _read_confinement_hook(repo_root),
                    _submit_validation_hook(self.on_event),
                ]
            },
        )
        if not captured:
            raise RuntimeError(f"agent did not call {_TOOL_SUBMIT}")
        return QuizDraft.model_validate(captured[0])
```

- [ ] **Step 2: Update the Protocol**

In `src/cognit/engine/llm.py`: change the import line to `from cognit.engine.models import MermaidSet, MermaidSpec, QuizDraft` and replace the `generate_quiz_outline` method with:

```python
    def draft_quiz(self, req: GenerateRequest) -> QuizDraft:
        """Produce the complete quiz in one agentic call. Mermaid questions are
        fully rendered (4 diagrams + the correct key); a validation hook ensures
        every diagram parses and the four are visually uniform."""
```

(Leave `generate_mermaid_set` in the Protocol for now — removed in Task 4.)

- [ ] **Step 3: Update `FakeLLM`**

In `src/cognit/engine/llm_fake.py`: change the models import to `from cognit.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizDraft`, rename the ctor arg and method:

```python
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
```

(Leave `generate_mermaid_set` and `grade_open` as-is.)

- [ ] **Step 4: Make `generate.py` a thin one-stage orchestrator**

Replace the entire body of `src/cognit/engine/generate.py` above `_neutralize_mermaid_labels` (i.e. the module docstring, imports, `_validate_mermaid`, `_validate_set`, `_render_mermaid_with_retry`) and the `generate_quiz` function, keeping `_neutralize_mermaid_labels` intact. New top-of-file + new `generate_quiz`:

```python
"""Single-stage quiz generation.

One agentic call (`llm.draft_quiz`) produces the complete quiz with mermaid
fully rendered; a submit-validation hook inside the adapter guarantees every
diagram parses and the four options are visually uniform. This module just
builds the request, wraps the draft into a `Quiz`, and shuffles mermaid option
labels (defense-in-depth against the model's correct-answer-position bias).
"""

import random

from cognit.engine.llm import GenerateRequest, LLMClient
from cognit.engine.models import MermaidQuestion, Question, Quiz


def _neutralize_mermaid_labels(quiz: Quiz, rng: random.Random | None = None) -> Quiz:
    ...  # UNCHANGED — keep the existing implementation verbatim


def generate_quiz(
    *,
    pr_title: str,
    pr_body: str,
    pr_number: int,
    pr_url: str,
    branch: str,
    llm: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Quiz:
    req = GenerateRequest(
        pr_title=pr_title,
        pr_body=pr_body,
        pr_number=pr_number,
        pr_url=pr_url,
        branch=branch,
        model=model,
    )
    draft = llm.draft_quiz(req)
    quiz = Quiz(version="1", pr_number=pr_number, questions=draft.questions)
    return _neutralize_mermaid_labels(quiz)
```

Note the removed imports (`ThreadPoolExecutor`, `as_completed`, `ValidationError`, `is_valid_mermaid`, `MermaidPlaceholder`, `MermaidSet`) and removed `max_mermaid_retries`/`max_mermaid_workers` params and the 6-line race comment. `_neutralize_mermaid_labels` keeps using `random`, `MermaidQuestion`, `Question`, `Quiz`.

- [ ] **Step 5: Update `test_llm_claude_agent.py`**

Change the models import (line 21) to `from cognit.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizDraft`. Replace the two outline tests (`test_generate_quiz_outline_restricts_to_readonly_tools`, `test_generate_quiz_outline_registers_pr_diff_and_submit_tools`, `test_generate_quiz_outline_raises_when_submit_not_called`) with:

```python
def test_draft_quiz_restricts_to_readonly_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = QuizDraft(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    seen: dict[str, Any] = {}

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        seen["options"] = options
        seen["prompt"] = prompt
        asyncio.run(handler(canned.model_dump()))

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo/root")

    llm = ClaudeAgentLLM()
    out = llm.draft_quiz(_req())

    assert out == canned
    opts = seen["options"]
    assert opts.tools == ["Read", "Grep", "Glob"]
    assert opts.allowed_tools == [
        "Read", "Grep", "Glob",
        "mcp__cognit__pr_diff", "mcp__cognit__submit_quiz",
    ]
    assert opts.cwd == "/repo/root"
    assert opts.max_turns == 30
    assert opts.permission_mode == "bypassPermissions"
    # Two PreToolUse matchers: read-confinement + submit-validation.
    matchers = [m.matcher for m in opts.hooks["PreToolUse"]]
    assert matchers == ["Read|Grep|Glob", "mcp__cognit__submit_quiz"]
    assert "feat/x" in seen["prompt"]


def test_draft_quiz_registers_pr_diff_and_submit_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {}
    real_create = llm_mod.create_sdk_mcp_server

    def spy_create(*args: Any, **kwargs: Any) -> Any:
        recorded["tools"] = kwargs["tools"]
        return real_create(*args, **kwargs)

    canned = QuizDraft(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        asyncio.run(handler(canned.model_dump()))

    monkeypatch.setattr(llm_mod, "create_sdk_mcp_server", spy_create)
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(llm_mod, "fetch_pr_diff", lambda url: f"DIFF-FOR::{url}")

    llm = ClaudeAgentLLM()
    out = llm.draft_quiz(_req())
    assert out == canned

    tools = recorded["tools"]
    assert [t.name for t in tools] == ["pr_diff", "submit_quiz"]
    pr_diff_tool = next(t for t in tools if t.name == "pr_diff")
    result = asyncio.run(pr_diff_tool.handler({}))
    assert result["content"][0]["text"] == "DIFF-FOR::https://github.com/o/r/pull/7"


def test_draft_quiz_raises_when_submit_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_does_nothing())
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo")
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_quiz"):
        llm.draft_quiz(_req())
```

(Leave the `_invoke_tool`, `generate_mermaid_set`, and `grade_open` tests untouched — Task 4 removes the mermaid ones.)

- [ ] **Step 6: Rewrite `test_generate.py`**

The stage-2 tests (`test_generate_renders_mermaid_via_subagent…`, `test_generate_drops_invalid_mermaid`, `test_generate_retries_artisan_then_succeeds`, `test_generate_survives_validation_error_from_artisan`) describe behavior that no longer exists. Replace the whole file with:

```python
from cognit.engine.generate import generate_quiz
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import (
    MCQQuestion,
    MermaidQuestion,
    OpenQuestion,
    QuizDraft,
)


def _draft_with_mermaid() -> QuizDraft:
    return QuizDraft(
        questions=[
            MCQQuestion(id="q1", prompt="why lock?", options=["safety", "speed"], answer="safety"),
            MermaidQuestion(
                id="q2",
                prompt="which flow?",
                options={
                    "A": "flowchart LR\nA-->B",
                    "B": "flowchart LR\nB-->A",
                    "C": "flowchart LR\nA-->C",
                    "D": "flowchart LR\nC-->B",
                },
                answer="A",
            ),
            OpenQuestion(id="q3", prompt="rationale?", rubric="thread safety"),
        ]
    )


def test_generate_wraps_draft_into_quiz_and_passes_through() -> None:
    draft = _draft_with_mermaid()
    out = generate_quiz(
        pr_title="add lock",
        pr_body="",
        pr_number=1,
        pr_url="https://github.com/o/r/pull/1",
        branch="br",
        llm=FakeLLM(canned_draft=draft),
    )
    assert out.pr_number == 1
    assert out.questions[0] == draft.questions[0]
    assert out.questions[2] == draft.questions[2]
    mq = out.questions[1]
    assert isinstance(mq, MermaidQuestion)
    assert set(mq.options.keys()) == {"A", "B", "C", "D"}
    # Label may be shuffled, but the correct source content is preserved.
    assert mq.options[mq.answer] == "flowchart LR\nA-->B"


def test_mermaid_labels_are_shuffled() -> None:
    seen_answer_keys: set[str] = set()
    for _ in range(20):
        out = generate_quiz(
            pr_title="t",
            pr_body="",
            pr_number=1,
            pr_url="https://github.com/o/r/pull/1",
            branch="br",
            llm=FakeLLM(canned_draft=_draft_with_mermaid()),
        )
        mq = out.questions[1]
        assert isinstance(mq, MermaidQuestion)
        assert mq.options[mq.answer] == "flowchart LR\nA-->B"
        seen_answer_keys.add(mq.answer)
    assert len(seen_answer_keys) > 1, "expected shuffle to vary the answer key over runs"
```

- [ ] **Step 7: Update `test_llm_fake.py`**

Replace `test_fake_returns_canned_outline` with the draft equivalent; leave the mermaid + grade fake tests (removed in Task 4). Change the import to `from cognit.engine.models import MCQQuestion, MermaidSet, QuizDraft` and:

```python
def test_fake_returns_canned_draft() -> None:
    canned = QuizDraft(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")],
    )
    llm: LLMClient = FakeLLM(canned_draft=canned)
    out = llm.draft_quiz(
        GenerateRequest(
            pr_title="t", pr_body="b", pr_number=1, pr_url="https://x/pull/1", branch="br"
        )
    )
    assert out == canned
```

- [ ] **Step 8: Update the inline fakes in `test_take.py`**

In `tests/cli/test_take.py`: change `from cognit.engine.models import MCQQuestion, QuizOutline` → `from cognit.engine.models import MCQQuestion, QuizDraft`. In each inline fake `LLMClient` (3 of them, ~lines 221, 262, 303), rename `def generate_quiz_outline(self, req)` → `def draft_quiz(self, req)`, change `QuizOutline.model_validate(...)`/`QuizOutline(...)` → `QuizDraft(...)`, and delete the `def generate_mermaid_set(self, spec, req)` stubs. Also update the `canned_outline=QuizOutline(...)` at line 24 to `canned_draft=QuizDraft(...)`.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. (`generate_mermaid_set` and the old models are still defined but now unused.)

- [ ] **Step 10: Commit**

```bash
git add src/cognit/engine/ tests/engine/test_llm_claude_agent.py tests/engine/test_generate.py tests/engine/test_llm_fake.py tests/cli/test_take.py
git commit -m "refactor(engine): single-stage quiz generation via draft_quiz + validation hook"
```

---

## Task 4: Delete the dead two-stage code

Now nothing uses the artisan path or the old models. Remove them.

**Files:** `src/cognit/engine/llm.py`, `llm_claude_agent.py`, `llm_fake.py`, `models.py`, and their tests.

- [ ] **Step 1: Remove `generate_mermaid_set` from the adapter**

In `src/cognit/engine/llm_claude_agent.py`: delete the `generate_mermaid_set` method (the block that builds the inline mermaid schema and calls `_invoke_tool`), delete `_format_misconceptions`, and delete the `_TOOL_MERMAID = "submit_mermaid_set"` constant. Update the models import to drop `MermaidSet, MermaidSpec, QuizOutline`: `from cognit.engine.models import MermaidQuestion, QuizDraft`. (Keep `MermaidQuestion` — the hook uses it.)

- [ ] **Step 2: Remove `generate_mermaid_set` from the Protocol and fake**

In `src/cognit/engine/llm.py`: delete the `generate_mermaid_set` method and change the import to `from cognit.engine.models import QuizDraft`. In `src/cognit/engine/llm_fake.py`: delete `generate_mermaid_set`, drop the `canned_mermaid` ctor arg and `self._mermaid`, and change the import to `from cognit.engine.models import MCQQuestion, QuizDraft`.

- [ ] **Step 3: Delete the dead models**

In `src/cognit/engine/models.py`: delete `MermaidSpec`, `MermaidPlaceholder`, the `OutlineQuestion` union, `MermaidSet`, and `QuizOutline` (and the now-orphaned comment block introducing the "internal-only types"). Keep `MCQQuestion`, `MermaidQuestion`, `OpenQuestion`, `TrueFalseQuestion`, `Question`, `Quiz`, `QuizDraft`, and the Answer/Results models.

- [ ] **Step 4: Delete the dead tests**

In `tests/engine/test_llm_claude_agent.py`: delete `test_generate_mermaid_set_calls_mermaid_tool` and `test_generate_mermaid_set_raises_when_tool_not_called`, and remove `MermaidSet, MermaidSpec` from its import (→ `from cognit.engine.models import MCQQuestion, QuizDraft`). In `tests/engine/test_llm_fake.py`: delete `test_fake_returns_canned_mermaid_set` and drop `MermaidSet` from its import (→ `from cognit.engine.models import MCQQuestion, QuizDraft`).

- [ ] **Step 5: Run ruff + mypy + full suite**

Run: `uv run ruff check . && uv run mypy && uv run pytest -q`
Expected: PASS, no unused-import (F401) errors, no type errors.

- [ ] **Step 6: Commit**

```bash
git add src/cognit/engine/ tests/engine/test_llm_claude_agent.py tests/engine/test_llm_fake.py
git commit -m "refactor(engine): delete two-stage mermaid artisan path and dead models"
```

---

## Task 5: Merge the prompts

Fold the artisan rules into the author prompt and update the tool name. Not test-covered; verify by reading.

**Files:** `src/cognit/engine/prompts/system_generate.txt`, `generate.txt`; delete `system_mermaid.txt`, `mermaid.txt`.

- [ ] **Step 1: Rewrite `system_generate.txt`**

Replace the **`mermaid`** bullet under "Choosing question types" and the entire "## Mermaid spec quality (when emitting one)" section with the inline-rendering version. Replace those parts with:

```
- **`mermaid`** — for control flow, data flow, sequence of calls, or component-interaction questions where a diagram is the clearest way to ask "what happens, in what order, between which parts." You render the diagrams yourself: emit 4 mermaid sources keyed `A`/`B`/`C`/`D` (one correct + three plausible distractors) and set `answer` to the correct key. Decide the *right thing to diagram* and the three *misconceptions* worth probing.

## Drawing mermaid diagrams (when you include a mermaid question)

You produce all four diagrams. Rule 1 below is the most important: the correct answer must NOT look cleaner, bigger, or more complete than the distractors, or the answer leaks.

1. **Uniform style across all 4.** Same diagram type and direction (`flowchart LR`, `sequenceDiagram`, …) for all four. Same node-naming convention. Node count within ±1 across all four. Similar edge count. Either all edges are labelled or none are. A validator rejects submissions where the four diagrams differ in header/direction or size — keep them comparable.
2. **Each distractor encodes one SPECIFIC misconception.** Pick exactly three wrong mental models the author might hold, and make each distractor the correct diagram *mutated* in that one way (swap an edge, drop a node, reorder a sequence, add a fork that isn't there). Small mutations that still look plausible — never random or scrambled diagrams.
3. **Safe syntax only.** Allowed headers: `flowchart`, `sequenceDiagram`, `classDiagram`, `stateDiagram-v2`. No HTML in labels, no `classDef`/`style`/`linkStyle`/theme directives, no icons, no subgraphs. Keep labels 1–4 words. Balanced brackets.
4. **Never start a node/edge label with `/` or `\`** — mermaid reads `[/text]` as a parallelogram. Wrap URL-like paths in quotes: `["/submit endpoint"]`, not `[/submit endpoint]`.
5. **Anti-leak:** never put words like "correct"/"wrong"/"right"/"bad" in node names, edge labels, or comments.
6. **Validity:** every diagram must parse as valid mermaid.

A validator parses every diagram and checks the four are uniform when you submit. If it rejects any, you'll get the reasons back — fix those diagrams and submit the whole quiz again.
```

Then update the "## Output" section to:

```
## Output

Submit the complete quiz — every mermaid question fully rendered — via the `submit_quiz` tool. The tool's schema enforces the structure; you do not need to repeat it.
```

(Leave the "## Frame", "## Input handling", and quality-bar sections unchanged, including the prompt-injection guard.)

- [ ] **Step 2: Update `generate.txt`**

Replace step 4 (`4. Submit your outline via the `submit_quiz_outline` tool.`) with:

```
4. Submit the complete quiz — mermaid questions fully rendered — via the `submit_quiz` tool. A validator checks every diagram; if it rejects any, fix those and submit again.
```

- [ ] **Step 3: Delete the artisan prompts**

```bash
git rm src/cognit/engine/prompts/system_mermaid.txt src/cognit/engine/prompts/mermaid.txt
```

- [ ] **Step 4: Smoke-check prompt loading**

Run: `uv run python -c "from cognit.engine.llm_claude_agent import _load_prompt; print(_load_prompt('system_generate.txt')[:40]); print(_load_prompt('generate.txt')[:40])"`
Expected: prints the first lines of both, no `FileNotFoundError`.

- [ ] **Step 5: Commit**

```bash
git add src/cognit/engine/prompts/
git commit -m "feat(prompts): merge mermaid artisan rules into the single-stage author prompt"
```

---

## Task 6: Activity-feed labels + streaming fixtures

The emitted `tool` string changed (`submit_quiz_outline`→`submit_quiz`) and `submit_mermaid_set` is gone. Update the UI label map and the test fixtures that hardcode the old strings.

**Files:** `src/cognit/server/assets/quiz.js`; `tests/server/test_ui_generating.py`, `test_progress.py`, `test_broker.py`; `tests/engine/test_drain_agent_sink.py`.

- [ ] **Step 1: Update `TOOL_LABELS`**

In `src/cognit/server/assets/quiz.js` (lines 629-633), replace the map with:

```javascript
const TOOL_LABELS = {
  submit_quiz: "Generating quiz",
  submit_grades: "Grading answers",
};
```

(`submit_grades` anticipates the grading-half plan; it's harmless now. `submit_quiz_outline` and `submit_mermaid_set` are removed.)

- [ ] **Step 2: Update the streaming test fixtures**

- `tests/engine/test_drain_agent_sink.py:45,50,51`: replace `"submit_quiz_outline"` → `"submit_quiz"` (the `_current_tool` assignment and both expected event `tool` values).
- `tests/server/test_progress.py:31-32` and `tests/server/test_broker.py:32-33`: replace `"submit_quiz_outline"` → `"submit_quiz"`.
- `tests/server/test_ui_generating.py:69-70`: replace `"submit_quiz_outline"` → `"submit_quiz"`, and update the label assertion at line 91 from `"Generating outline"` → `"Generating quiz"`.

- [ ] **Step 3: Run the affected tests**

Run: `uv run pytest tests/engine/test_drain_agent_sink.py tests/server/test_progress.py tests/server/test_broker.py tests/server/test_ui_generating.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cognit/server/assets/quiz.js tests/engine/test_drain_agent_sink.py tests/server/test_progress.py tests/server/test_broker.py tests/server/test_ui_generating.py
git commit -m "feat(ui): relabel activity feed for single-stage generation"
```

---

## Task 7: Docs, straggler sweep, and full verification

**Files:** `cognit-claude-sdk-usage.md`, `README.md`, `CHANGELOG.md`, `INTENTS.md`.

- [ ] **Step 1: Update `cognit-claude-sdk-usage.md`**

This doc describes the engine's SDK usage. Update: §1 call table (generation is now one call `draft_quiz` + `grade_open`); §4–6 (drop the two-stage/`_invoke_tool`-for-mermaid narrative, describe `draft_quiz` + the submit-validation hook); §8 (still one `asyncio.run` per call); §11 (`draft_quiz` inputs/outputs → `QuizDraft`); §12 (the capture path now also passes the validation hook); and the call-flow mermaid diagram (remove the ThreadPoolExecutor/artisan branch).

- [ ] **Step 2: Fix the other stale docs**

- `README.md:78-82`: the generation sequence diagram — replace "QuizOutline + mermaid specs" + "loop per mermaid placeholder / artisan call" with the single `draft_quiz` call + validation-hook self-correction.
- `CHANGELOG.md:42,47`: the "up-to-2 retries; drop mermaid Q + add replacement MCQ on terminal failure" line contradicts the new behavior — rewrite to describe single-stage generation with in-turn diagram self-correction (no drop, no replacement MCQ).
- `INTENTS.md:196`: replace the `generate_mermaid_set` mention with `draft_quiz`.

- [ ] **Step 3: Straggler grep — must return nothing in `src/` or `tests/`**

Run:
```bash
grep -rnE "generate_mermaid_set|generate_quiz_outline|submit_quiz_outline|submit_mermaid_set|MermaidSpec|MermaidPlaceholder|MermaidSet|OutlineQuestion|QuizOutline|canned_outline|canned_mermaid|system_mermaid|mermaid\.txt|max_mermaid_workers|max_mermaid_retries|_format_misconceptions" src/ tests/
```
Expected: no output. (Hits are allowed only under `docs/superpowers/plans/2026-05-22-*` — the historical plan.)

- [ ] **Step 4: Full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q`
Expected: all green.

- [ ] **Step 5: End-to-end acceptance (requires `claude login` + `gh auth`)**

Run: `COGNIT_LOG_LEVEL=DEBUG uv run cognit take --pr <a small PR with a diagram-worthy change>`
Confirm:
- a single `submit_quiz` call (plus an in-turn re-submit if a diagram is rejected) in the debug logs;
- the live terminal shows one **"Generating quiz"** phase (not outline + per-diagram) and, on any rejection, the `checking diagrams…` / `⟳ fixing …` lines (not a silent pause);
- the quiz opens with rendered mermaid; grade + publish still work;
- **manual uniformity spot-check**: in a mermaid question, the correct diagram is not visually distinguishable (type/size/detail) from its three distractors.

- [ ] **Step 6: Commit**

```bash
git add cognit-claude-sdk-usage.md README.md CHANGELOG.md INTENTS.md
git commit -m "docs: describe single-stage quiz generation"
```

---

## Out of scope (separate plan)

The **grading half** (`grade_open` → batched `grade_open_batch` + a coverage hook, per spec steps 8-12) ships as its own plan/PR. This plan leaves `grade_open` and the grading flow untouched.
