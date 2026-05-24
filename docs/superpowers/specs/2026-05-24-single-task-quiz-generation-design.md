# Design: Efficient agentic flow — single-task generation + batched grading

**Status:** Designed — not yet implemented. Core mechanism spike-validated against live Sonnet (see "SDK facts").

**Date:** 2026-05-24

**Builds on:** `2026-05-23-agentic-outline-generation-design.md` (made the outline call agentic). This collapses the *second* generation stage into that same call, and separately collapses per-question open-answer grading into one batched call. The two halves (generation, grading) are independent and can ship as separate PRs.

## The agentic flow (the design in one picture)

The task has exactly two shapes of work, and the flow matches them: generation is genuinely *agentic* (explore the PR, reason about pedagogy, produce content — needs tools + a turn budget); grading is a *pure structured transform* (the grader sees only the rubric + answer, never the code — `system_grade.txt` — so no tools, no exploration). **One agent + one transform.** No subagents: SDK-native subagents lose on determinism, testability, and compose unproven-ly with our validated hook loop; the only thing they'd buy (mermaid-artisan persona isolation) is bought more cheaply by the validation hook + the merged prompt.

```
cognit take (cache miss)
  └─ ONE generation agent (agentic):
       pr_diff → Read/Grep/Glob (selective, repo-confined)
       → reason: which probes? for mermaid, which 3 misconceptions?
       → submit_quiz  (complete quiz, mermaid fully rendered)
       → PreToolUse hook:  shape → 4-opts/answer∈keys → mermaid syntax → UNIFORMITY
            └─ on any failure: deny + precise reason → agent self-corrects in-turn ⟲
       → captured → Quiz(pr_number) → _neutralize_mermaid_labels (position shuffle)

/submit
  ├─ MCQ / mermaid / T-F → deterministic in Python (no model)
  └─ open questions → ONE grade_open_batch call → [{question_id, score, feedback}]
            └─ PreToolUse hook: every open id covered, scores ∈ [0,100]
```

Cost per full take+grade cycle: **2 model calls** (+ cheap in-turn corrections), vs. today's `1 + N_mermaid` generation calls and `N_open` grading calls. The hook is the quality enforcer on everything mechanically checkable; the prompt + output contract carry what can only be judged semantically.

## Problem

Quiz generation is two LLM roles coordinated by Python (`src/cognit/engine/generate.py`):

1. **Outline** (`ClaudeAgentLLM.generate_quiz_outline`, agentic): reads the PR and emits questions, where mermaid questions are `MermaidPlaceholder`s carrying a `MermaidSpec` (a *description* of the wanted diagram), not rendered diagrams.
2. **Mermaid artisans** (`generate_mermaid_set`, single-shot): for each placeholder, a `ThreadPoolExecutor` fan-out renders 4 diagrams, validates them with `is_valid_mermaid` *after* the call returns, and **retries up to twice or drops** the question on failure.

This split carries real weight: a second system prompt (`system_mermaid.txt`), two internal-only models (`MermaidSpec`, `MermaidPlaceholder`), a thread pool with rate-limit tuning (`max_mermaid_workers=1`), per-question retry loops, and shared mutable adapter state (`_current_tool`) that `generate.py:108-113` documents as *racing* whenever `max_mermaid_workers > 1`. Crucially, a diagram is validated only *after* a whole second-stage call completes, so one syntax error costs an entire fresh `generate_mermaid_set` call (or a silently dropped question).

The outline call is *already* an agent that reads the PR. A `PreToolUse` hook that `deny`s with a reason makes the agent self-correct **within the same `query()`** (spike-proven below). That lets the one agent render the diagrams itself and fix invalid ones in-turn — removing the entire second stage.

Separately, **grading open answers is N model calls** — `grade.py:40` loops `grade_open` once per open question, each spawning the `claude` binary afresh. The grader uses no tools (`system_grade.txt`: rubric + answer only). N near-identical structured-transform calls are pure overhead; one batched call does the same work.

## Approach

**One agent task** produces the complete quiz. The generation agent submits a quiz whose mermaid questions are *fully rendered* `MermaidQuestion`s. A `PreToolUse` hook on the submit tool runs `is_valid_mermaid` over every diagram (and `QuizDraft` shape-validation) and `deny`s on failure with per-diagram reasons; the agent fixes them and re-submits in the same turn.

Key decisions:
- **One submit tool, final shapes.** The submit schema is `QuizDraft = {version, questions: list[Question]}` — the *final* `Question` union (with rendered `MermaidQuestion`), no `pr_number` (the orchestrator knows it). No more `MermaidPlaceholder`/`MermaidSpec`.
- **Validation moves into a hook, not a retry loop.** The hook is the gate; in-turn `deny`→correct replaces the second-stage call + Python retry. (Spike-proven.)
- **The hook enforces everything mechanically checkable — including uniformity.** Beyond shape + mermaid *syntax*, the hook enforces the mechanical half of the artisan's anti-leak rule (`system_mermaid.txt` rule 1): all 4 diagrams share the same header + flow direction, with node/edge counts within a ±1 band. This converts the single biggest quality risk of the collapse (the correct diagram "looking better" → answer leak) from a prompt-hope into a self-correcting invariant. What the hook *can't* judge (are the distractors *semantically* real misconceptions) stays prompt-driven.
- **Misconception discipline is prompt-enforced, not a schema field.** The old `spec.misconceptions` field was a *communication channel* between the separate author and artisan; with one agent it's redundant (the agent holds the misconceptions while drawing). The discipline — exactly 3 distractors, each a specific wrong mental model, each a small mutation of the correct diagram — moves verbatim into the merged prompt. We do **not** reintroduce a draft-vs-final question split to capture it.
- **No graceful-drop.** If a diagram won't validate within the 30-turn budget, generation fails to the broker error screen. Accepted regression from today's drop-and-continue — first-try mermaid validity is high (spike: 0 real failures), and in-turn correction is cheap.
- **Grading batches N→1.** Deterministic questions (MCQ/mermaid/T-F) grade in Python with no model. Open questions move from N `grade_open` calls to one `grade_open_batch` call returning an array of per-question results, gated by its own validation hook. The grading prompt adds "grade each question independently against its own rubric" to blunt cross-question anchoring.
- **Keep the label shuffle.** `_neutralize_mermaid_labels` stays — load-bearing against the model's bias to put the correct answer under "A" (a bias the uniformity hook does *not* address — it checks visual parity, not position).
- **Keep `pr_diff` + the read-confinement hook.** The agent still fetches its own diff and is still confined to the repo root.

## Implementation (file by file)

Do in this order — signatures must agree before it type-checks.

1. **`src/cognit/engine/models.py`:** delete `MermaidSpec`, `MermaidPlaceholder`, the `OutlineQuestion` union (lines 63-88), **and `MermaidSet`** (lines 98-110 — now dead; only `generate_mermaid_set` and its callers used it). Rename `QuizOutline` → `QuizDraft`, with `questions: list[Question]` (the final union incl. `MermaidQuestion`); keep `version: Literal["1"]`. `MCQQuestion`/`MermaidQuestion`/`OpenQuestion`/`TrueFalseQuestion`/`Question`/`Quiz` unchanged. Note: `MermaidQuestion` does **not** model-enforce "exactly 4 options keyed A–D" (that invariant lived in `MermaidSet._shape_ok`); the submit-validation hook re-establishes it (step 4).

2. **`src/cognit/engine/llm.py` — `LLMClient`:** remove `generate_mermaid_set`. Rename `generate_quiz_outline(req) -> QuizOutline` → **`draft_quiz(req) -> QuizDraft`** (distinct name from the `generate.py::generate_quiz` orchestrator to avoid confusion). Drop the now-unused `MermaidSet`/`MermaidSpec` imports; import `QuizDraft`. `GenerateRequest` unchanged.

2a. **`src/cognit/engine/llm_fake.py` — `FakeLLM`** (the in-package test double; implements `LLMClient`, so it must change in lockstep): rename `generate_quiz_outline`→`draft_quiz` and the `canned_outline: QuizOutline`→`canned_draft: QuizDraft` ctor arg; **delete `generate_mermaid_set`, the `canned_mermaid` arg, and the `MermaidSet`/`MermaidSpec` imports**. A canned draft now carries rendered `MermaidQuestion`s directly.

3. **`src/cognit/engine/llm_claude_agent.py` (core change):**
   - Add `_submit_validation_hook() -> HookMatcher`, `matcher="mcp__cognit__submit_quiz"`. The async hook: (a) `draft = QuizDraft.model_validate(tool_input)` — on `ValidationError` (or a non-dict/garbage `tool_input`), `deny` with the error summary so the agent fixes shape in-turn; (b) iterate the **validated `draft.questions`** (never the raw dict, so a malformed option can't throw inside the hook). For each `MermaidQuestion`:
     - **shape:** exactly 4 options and `answer` ∈ its keys — the count/answer-key part of the old `MermaidSet._shape_ok`; do **not** require the literal keys be A–D, since `_neutralize_mermaid_labels` relabels them anyway.
     - **syntax:** for each option source `await asyncio.to_thread(is_valid_mermaid, src, strict=False)` (explicit `strict=False` preserves today's `_validate_set` semantics).
     - **uniformity** (new — the mechanical half of `system_mermaid.txt` rule 1): a `_uniformity_failures(options) -> list[str]` helper checks the 4 sources share (i) the same diagram header/type — reuse `mermaid._ALLOWED_HEADERS` to extract it, (ii) the same flow direction (`LR`/`TD`/… for flowcharts), (iii) node and edge counts within a ±1 band (heuristic counts: node-declaration tokens; edge operators `-->`/`---`/`->>`/`-->>` etc.). These are deliberately coarse proxies — they catch the common leak (correct diagram bigger/different type), not subtle ones.
     Collect all failures as precise strings (`f"question {id} option {label}: invalid mermaid"`, `f"question {id}: option B has 9 nodes, others have 4 — make all four comparable"`). (c) any failures → `deny` listing them, else `{}` (allow).
   - **Stream the validation/retry to the activity feed.** Build `_submit_validation_hook` as a closure over the adapter (or pass the `on_event` sink in) so it can emit while it runs — otherwise the hook is invisible to the UI and the `mmdc` validation reads as a stall, with the agent silently re-submitting. Emit a `{"kind":"text","text":"checking diagrams…","tool":self._current_tool}` when the hook fires and a `{"kind":"text","text":"⟳ fixing N diagram(s): …","tool":self._current_tool}` on `deny`. `on_event`/`broker.emit` is thread-safe, and calling it from the hook coroutine is a cheap lock+append. (Without this, the only feed signal during a correction is whatever prose the model happens to emit.)
   - Rename `generate_quiz_outline` → `draft_quiz`. Register `pr_diff` + **`submit_quiz`** (was `submit_quiz_outline`) on the server; submit schema = `QuizDraft.model_json_schema()`. `tools=["Read","Grep","Glob"]`, `cwd=_repo_root()`, `max_turns=_OUTLINE_MAX_TURNS` (30), `allowed_tools=[*_OUTLINE_BUILTIN_TOOLS, "mcp__cognit__pr_diff", "mcp__cognit__submit_quiz"]`. **`hooks={"PreToolUse": [_read_confinement_hook(repo_root), _submit_validation_hook()]}`** (two matchers coexist). After drain: `captured` empty → `RuntimeError("agent did not call submit_quiz")`; else `QuizDraft.model_validate(captured[0])` (near-guaranteed to pass, since the hook already validated — defense-in-depth).
   - Delete `generate_mermaid_set`, `_format_misconceptions` (dead), the `_TOOL_MERMAID` constant; rename `_TOOL_OUTLINE`→`_TOOL_SUBMIT="submit_quiz"`. `_invoke_tool` and `grade_open` unchanged. `_current_tool`/`on_event`/`_forward_activity` unchanged in shape (the shared-state race is now moot — generation is a single call), **but note the emitted activity `tool` string changes `submit_quiz_outline`→`submit_quiz`**, which is consumed by the UI label map (step 5a) and several broker/streaming test fixtures (step 6).

4. **`src/cognit/engine/generate.py`:** delete stage 2 entirely — `_render_mermaid_with_retry`, `_validate_set`, `_validate_mermaid`, the `ThreadPoolExecutor` block, and the `ThreadPoolExecutor`/`as_completed`/`ValidationError`/`is_valid_mermaid`/`MermaidPlaceholder`/`MermaidSet` imports. `generate_quiz(...)` drops `max_mermaid_retries`/`max_mermaid_workers` **and the now-meaningless thread-pool-race comment block above them (lines 108-113)**, and becomes: build `req` → `draft = llm.draft_quiz(req)` → `quiz = Quiz(version="1", pr_number=pr_number, questions=draft.questions)` → `return _neutralize_mermaid_labels(quiz)`. Keep `_neutralize_mermaid_labels` and its `MermaidQuestion` import.

5. **Prompts `src/cognit/engine/prompts/`:**
   - `system_generate.txt`: fold in the mermaid-artisan rules from `system_mermaid.txt` **verbatim where possible** (uniform style, 1 correct + 3 misconception-based distractors, the allowed headers/shapes `mermaid.py` enforces, "small and strictly parseable"). Add: *"A validator parses every diagram when you submit; if it rejects any, fix those and resubmit the whole quiz."* Preserve the prompt-injection guard.
   - `generate.txt`: final step → "submit the complete quiz (mermaid fully rendered) via `submit_quiz`; expect validator feedback to fix invalid diagrams in place." (Update the `submit_quiz_outline` tool name → `submit_quiz` here and in `system_generate.txt:37`.)
   - Delete `system_mermaid.txt` and `mermaid.txt`.

5a. **`src/cognit/server/assets/quiz.js` — activity labels (`TOOL_LABELS`, `quiz.js:629-633`):** all three entries map to renamed/removed tools — update the whole map:
   - `submit_quiz_outline: "Generating outline"` → `submit_quiz: "Generating quiz"`
   - `submit_mermaid_set: "Drawing diagram"` → **remove** (tool deleted)
   - `submit_grade: "Grading answer"` → `submit_grades: "Grading answers"`
   Without this the feed falls through to the raw tool string (`quiz.js:641`).

6. **Tests:**
   - `tests/engine/test_generate.py`: rewrite around `draft_quiz` — the canned draft carries a rendered `MermaidQuestion` (no placeholder/`MermaidSet`). Drop `generate_quiz(...)`'s `max_mermaid_*` kwargs and the retry/drop tests (that behavior no longer exists). Add: `generate_quiz` wraps the draft into `Quiz(pr_number=…)` and shuffles labels.
   - `tests/engine/test_llm_fake.py`: update for the new `FakeLLM` surface (`draft_quiz`/`canned_draft`; remove `generate_mermaid_set`/`canned_mermaid`).
   - `tests/engine/test_llm_claude_agent.py`: rename outline tests → `draft_quiz`; assert built `ClaudeAgentOptions` has `tools==["Read","Grep","Glob"]`, `allowed_tools` includes `mcp__cognit__submit_quiz`, `max_turns==30`, and **two** `PreToolUse` matchers. Delete the two `generate_mermaid_set` tests; keep the grade `_invoke_tool` test.
   - `tests/cli/test_take.py`: the inline fake `LLMClient`s implement `generate_quiz_outline`/`generate_mermaid_set` and import `QuizOutline` — rename to `draft_quiz`, drop the `generate_mermaid_set` stubs, switch `QuizOutline`→`QuizDraft`.
   - **New** `tests/engine/test_submit_validation_hook.py`: drive the hook directly (no live agent), **monkeypatching `is_valid_mermaid`** for determinism (don't depend on mmdc/docker in CI). Cases: all-valid quiz → `{}`; one invalid diagram → `deny` whose reason names the question/option; wrong option count → `deny`; shape-invalid `tool_input` → `deny` with a Pydantic message.
   - **Activity-string fixtures (consequence of the `submit_quiz_outline`→`submit_quiz` rename):** `tests/server/test_broker.py:32-33`, `tests/server/test_progress.py:31-32`, `tests/engine/test_drain_agent_sink.py:45,50-51` emit/assert the old tool string as opaque fixture data — update to `submit_quiz`. `tests/server/test_ui_generating.py:69-70` emits it **and asserts `"Generating outline"` renders (`:91`)** — update both the emitted string and the expected label to match the new `quiz.js` map (`"Generating quiz"`).
   - `tests/engine/test_mermaid.py` unchanged. `tests/server/test_submit_with_claude_agent.py` (loop-in-loop grading guard) unchanged and must still pass.

7. **Docs:** update `cognit-claude-sdk-usage.md` to the new flow (§1 call table → `draft_quiz` + `grade_open_batch`; rewrite §4–6, §8, §11, §12 and the call-flow diagram; drop the mermaid-artisan/ThreadPoolExecutor narrative). Confirmed-stale spots to fix: `README.md:78-82` (sequence diagram still shows "QuizOutline + mermaid specs" + "loop per mermaid placeholder / artisan call"); `CHANGELOG.md:42,47` ("up-to-2 retries; drop mermaid Q + add replacement MCQ on terminal failure" — contradicts the new no-drop/no-replacement behavior); `INTENTS.md:196` (mentions `generate_mermaid_set`).

### Grading half (independent — separate PR)

Orthogonal to the generation collapse; mechanically the same "one well-defined call + a validation hook" pattern.

8. **`src/cognit/engine/llm.py`:** replace `grade_open(question_prompt, rubric, answer) -> tuple[int,str]` with `grade_open_batch(items: list[OpenGradeItem]) -> dict[str, tuple[int,str]]` (`OpenGradeItem = {question_id, prompt, rubric, answer}`). Add submit models in `models.py`: `OpenGrade = {question_id: str, score: int[0..100], feedback: str}`, `GradeBatch = {grades: list[OpenGrade]}`.

9. **`src/cognit/engine/llm_claude_agent.py`:** extend `_invoke_tool` to accept an optional `hooks` param (currently always builds with `hooks=None`). Implement `grade_open_batch`: `submit_grades` tool with schema `GradeBatch.model_json_schema()`, `system=system_grade.txt`, `user=grade_open_batch.txt` (all items rendered), `tools=[]`, plus a `_grade_coverage_hook(expected_ids)` `PreToolUse` hook on `mcp__cognit__submit_grades` that `deny`s if any expected `question_id` is absent or unknown ids appear → in-turn fix. Map captured `grades` → `{id: (clamp(score,0,100), feedback)}`. Delete `grade_open`; keep `_invoke_tool`.

10. **`src/cognit/engine/grade.py`:** gather all `OpenQuestion`s into one `grade_open_batch` call (guard: skip the call entirely when there are none); map results back by `question_id` (a missing id floors to score 0 / empty feedback — defensive, though the coverage hook should prevent it). Deterministic branches and `app.py`'s `asyncio.to_thread(grade, …)` offload unchanged.

11. **`src/cognit/engine/llm_fake.py` + prompts:** `FakeLLM.grade_open_batch` returns canned per-id results. `system_grade.txt` += "grade each question strictly and independently against its own rubric; do not let one answer influence another." Replace `grade_open.txt` with `grade_open_batch.txt` rendering `<item id="…"><question/><rubric/><answer/></item>` per open question.

12. **Tests (grading):** `tests/engine/test_grade.py` (batch path + the no-open-questions skip + missing-id floor); `tests/engine/test_llm_claude_agent.py` (`grade_open_batch` builds the batch tool, `tools==[]`, wires the coverage hook); new coverage-hook unit test (missing id → deny); `tests/engine/test_llm_fake.py`. Any caller/test of the old `grade_open` updates to the batch signature.

## SDK facts (verified by spike — `claude_agent_sdk` 0.2.85, live Sonnet)

- A `PreToolUse` `HookMatcher` with `matcher="mcp__cognit__submit_quiz"` **fires on the in-process MCP submit tool**. Multiple matchers in one `PreToolUse` list coexist (read-confinement on `Read|Grep|Glob` + validation on the submit tool).
- `permissionDecision: "deny"` + `permissionDecisionReason` is **delivered to the model, which re-calls the tool in the same `query()`** and corrects per the reason. Trivial spike: 3 turns; full-quiz spike: forced denial → agent re-submitted the whole quiz → 4 turns, `is_error=False`.
- `is_valid_mermaid` (shells out to `mmdc`) runs cleanly inside the async hook via `asyncio.to_thread` — same offload pattern as `server/app.py:123`.
- A full nested quiz (discriminated `Question` union, 5 questions incl. 2 mermaid) **submits in one call and validates against the `QuizDraft` Pydantic model**. The agent produced valid mermaid first-try (0 real validation failures) with `mmdc` present.

## Residual risk

- **No graceful-drop (accepted):** a diagram unfixable within 30 turns fails the run. Spike evidence says this is rare; if it bites in practice, revisit with allow-through-after-N (the design we explicitly deferred).
- **Whole-quiz re-submit on correction:** a denial re-sends the full quiz payload (more output tokens), but stays in one `query()` — far cheaper than today's fresh `generate_mermaid_set` call.
- **Validator strength is host-dependent:** `mmdc` > docker > Python-regex backstop. On a host with neither real validator, the hook degrades to the regex check — identical to today's `_validate_set` behavior, so no regression.
- **Diagram uniformity (semantic remainder):** the hook now *enforces* the mechanical half of uniformity (same header/direction, node/edge ±1), which closes the biggest leak. What remains is the semantic half — a distractor could be the same size/type yet still "look obviously wrong" or the correct one subtly more coherent. That stays prompt-driven (the merged `system_mermaid.txt` rules + `_neutralize_mermaid_labels` for position). Verify by manual spot-check (Verification step 4).
- **Uniformity proxies are coarse (tuning risk):** the ±1 node/edge band is a heuristic. Too tight → false denials that make the agent thrash on legitimate diagrams (wasting turns toward the 30 budget); too loose → leaks slip through. The ±1 band matches the artisan prompt's own rule, but the node/edge *counting* is regex-ish and approximate. Start at ±1, treat as tunable, and watch turn counts in the end-to-end run.
- **Batch grading anchoring:** scoring all open answers in one call risks halo/contrast effects (a strong Q1 answer inflating Q2). Mitigated by the explicit "grade each independently against its own rubric" instruction and per-item structure; the upside is N→1 subprocess spawns. If anchoring shows up, the fallback is small-batch or per-question — but don't pre-optimize for it.
- **Streaming granularity / feed heartbeat (decision):** we consume **complete messages** from `query()` (`include_partial_messages` stays `False`) and surface them as an *activity log*, not a token-stream. This is idiomatic for the log use-case and the 500ms `/progress` poll caps the benefit of finer granularity anyway. But the collapse to one big generation turn removes the per-call `step` heartbeat the old `1+N` flow gave, so a long single authoring message can leave the feed quiet. Discrete pulses still come from tool-use lines (`pr_diff`/`Read`/`Grep`) and the new validation events. **Upgrade trigger:** if the live terminal feels stalled mid-generation, set `include_partial_messages=True` and have `_forward_activity` accumulate `StreamEvent` `content_block_delta` text (deduping against the final assembled `AssistantMessage`), and tighten the poll or move `/progress` to SSE. Not in v1 — it's a real complexity add for modest gain.

## Verification

1. `uv run ruff check . && uv run ruff format --check . && uv run mypy`.
2. `uv run pytest -q` — engine/cli/ghio/agent tests, the new hook unit test, and the loop-in-loop grading guard.
3. Grep for stragglers (none in `src/` or `tests/`; the `docs/superpowers/plans/2026-05-22-*` historical plan may keep them): `generate_mermaid_set`, `generate_quiz_outline`, `submit_quiz_outline`, `MermaidSpec`, `MermaidPlaceholder`, `MermaidSet`, `OutlineQuestion`, `QuizOutline`, `canned_outline`, `canned_mermaid`, `system_mermaid.txt`, `mermaid.txt`, `max_mermaid_workers`, `max_mermaid_retries`, `_format_misconceptions`; and for the grading half, the bare `grade_open(` call site (now `grade_open_batch`) and `grade_open.txt` (now `grade_open_batch.txt`).
4. End-to-end (`claude login` + `gh auth`): `cognit take --pr <small PR with a diagram-worthy change>`. With `COGNIT_LOG_LEVEL=DEBUG`, confirm a single `submit_quiz` call (plus in-turn re-submit if a diagram is invalid), the quiz opens with rendered mermaid, and the live terminal shows one **"Generating quiz"** phase (not outline + per-diagram phases). Submit answers and confirm grading runs as **one** `grade_open_batch` call (not N), the feed shows one **"Grading answers"** phase, and publish still works. **Acceptance:** (a) the agent self-corrects in-turn rather than failing when a diagram is invalid, and the feed shows the `"checking diagrams…"`/`"⟳ fixing …"` lines (not a silent pause); (b) **manual uniformity spot-check** — in a generated mermaid question the correct diagram is not visually distinguishable (size/detail/polish) from its three distractors; (c) a quiz with **≥2 open questions** grades all of them in a single call with per-question feedback.
