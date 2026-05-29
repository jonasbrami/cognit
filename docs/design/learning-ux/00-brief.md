# cognit learning-acceleration UX — brief

## Original prompt

> go on latest main, investigate current interface and tell me how we could improve the user interface, the goal is to accelerate the learning process and leverage as much as possible claude to accelerate learning

Follow-up: split into multiple acceptable PR groups, write a design doc per task, test everything.

## Current interface (summary of findings)

`cognit take` launches Claude Code as a confined host session that reads the PR diff and renders a quiz in the browser. The UI today (`src/cognit/mcp/assets/{index.html,quiz_mcp.js,styles.css}`):

- GitHub-styled single page; all questions listed at once.
- Flow: Waiting → Answering (all visible) → Submit → Results (per-question scorecard + explanations) → optional Publish.
- Sidebar with progress dots and per-question status.
- Steering ("harder", "skip", "focus this file") is **terminal-only** — requires context-switching out of the browser.
- Browser and host communicate only via `QuizState` (write-through JSON snapshot, plus the shared in-process object).
- No code context shown next to questions; no per-question feedback; no confidence rating; no retry/drill loop; no cross-PR memory.

## The architectural constraint that shapes everything

This is the load-bearing fact, verified against the code:

- `cli/take.py:175` does `os.execvpe("claude", ...)`. So `claude` (the host) **owns the terminal TTY**, and the MCP server (`python -m cognit.mcp`) is its **child**. The FastAPI web app is a daemon thread *inside that child* (`server.py:225`).
- The web app therefore has **no handle on the host's stdin**. The only browser→host data path is the shared in-process `QuizState` (`state.py`), which the host reads *only when it chooses to call an MCP tool*.
- The host is **strictly turn-based**: the kickoff ends with "Then wait" (`launch.py:31`). After rendering the quiz it blocks and acts only when the user types in the terminal. There is **no polling loop, notification, or wake path** today.

**Consequence.** Features whose value is "do something in the browser and the host reacts *without me switching to the terminal*" — steering chips, drill-this, activity panel — depend on a **host-wake mechanism that does not exist and is not yet designed**. Every other feature is independent of the host's turn loop and carries no such risk.

Two more facts that reshape the feature framing:

- **Grading already runs in the web process.** `web.py:77` (`POST /grade`) calls `grade_state` directly, bypassing the host. So immediate feedback, confidence, and teach-back need no host involvement.
- **`/state` already ships every correct answer to the browser.** `state.snapshot()` (`state.py:93`) dumps each question's `answer` field and `web.py:60` serves it verbatim. So "exam mode" / "confidence before reveal" buy **no integrity** — the answer is always on the client. That is fine for an honor-system learning tool, but the design docs must not imply otherwise, and we are explicitly *not* changing it.

## The 10 features

| # | Feature | One-line summary |
|---|---|---|
| 1 | **Inline code context per question** | Collapsible diff hunk next to each question, using a new optional `anchor: {path, start_line, end_line}` field on Question. |
| 2 | **Per-question immediate feedback + exam-mode toggle** | Deterministic types (mcq/tf/mermaid) grade and reveal on commit, client-side (the answer is already in the page). Open questions still grade at submit. localStorage toggle preserves the current batch flow. Cosmetic only — not an integrity boundary. |
| 3 | **Confidence rating before reveal** | "How sure? 1–5" prompt prior to reveal; results surface miscalibration (high confidence + wrong). Honor-system: does not gate answer visibility. |
| 4 | **In-UI steering chips** | Per-question chips (Harder · Different angle · Explain first · Drill this · Skip) enqueue steer intents for the host. **Requires the host-wake mechanism (Track B).** |
| 5 | **Drill-this loop on wrong answers** | After a deterministic miss, CTA asks the host for 1–2 variant questions on the same concept, anchored to the same hunk. **Requires the host-wake mechanism (Track B).** |
| 6 | **Teach-back for missed questions** | One-sentence "why is the right answer right?" prompt after a miss; graded via the web-process open-grading path (`grade_state` / `mcp/grading.py`). Production beats recognition for retention. |
| 7 | **One-question-at-a-time card view** | Focused single-card view as default, with j/k or arrow nav across questions. "Show all" toggle in localStorage. |
| 8 | **Live host activity panel** | Sidebar mirror of host activity (tool name + path only — no args, no secrets); newest-first; ring-buffer capped at 50, held **in memory only** (excluded from `QuizState._persist`). **Coupled to the host-wake mechanism (Track B).** |
| 9 | **Diff coverage map** | Sidebar list of files in the diff with covered/uncovered markers based on feature-1 anchors. Display-only in Track A; the "ask host to cover this" steer is Track B. |
| 10 | **Cross-PR weak-concept memory** | `~/.cognit/history.json` persists missed-concept tags, **written post-grade by the orchestrator/web process** (not the confined host); recent weak concepts feed the host kickoff for spaced repetition across PRs. |

## Two-track roadmap

The brief originally sequenced these as 7 stacked PRs treating the backend as "pure plumbing." That is backwards: the backend hides the project's only real unknown (host-wake), while most of the learning value is independent of it. So the work splits into two tracks.

### Track A — ship now, no host-wake dependency (features 1, 2, 3, 6, 7, 9, 10)

Each PR is independently verifiable and invisible when its feature is ignored.

1. **PR A1 — anchors foundation (1).** Add optional `anchor` to the question models (`engine/models.py`); teach the generation prompt to emit it; backwards-compat load for old cached JSON (`state.py:_load`). Inline collapsible diff hunk in the UI via existing `file_diff` data. *Diff hunks must be DOM-built, never `innerHTML` — honor the textContent-only invariant.*
2. **PR A2 — diff coverage map (9).** Sidebar files-in-diff list with covered/uncovered markers derived from anchors; uses the existing `changed_files` overview. Display only (the "ask host to cover this" button is Track B — omit or stub).
3. **PR A3 — feedback loop (2 + 3 + 6).** Client-side immediate reveal for deterministic types; confidence prompt; teach-back grading reusing the web-process grading path (`web.py:77`, `mcp/grading.py`).
4. **PR A4 — one-question card view (7).** Pure frontend; j/k nav; "show all" localStorage toggle.
5. **PR A5 — cross-PR weak-concept memory (10).** `~/.cognit/history.json` module written post-grade by the web/orchestrator process; recent weak concepts injected into the host kickoff (`launch.py:_kickoff`).

### Track B — gated behind a host-wake spike (features 4, 5, 8)

All three require the host to react to a browser event without the user touching the terminal. **Run the spike before writing any design doc for them.**

**Spike: blocking long-poll MCP tool.** Prototype an `await_steer` MCP tool that the host calls in a loop — it blocks server-side on a `QuizState` condition until the browser enqueues a steer intent (or a bounded timeout, e.g. 25s), returns the intent, the host acts (`replace_question` / `set_quiz` / a new `append_questions`), then calls `await_steer` again. Change the kickoff from "Then wait" to "call `await_steer` in a loop to receive the reader's requests."

This is the *only* design that keeps the host driven by browser events while honoring the "browser ↔ host only via `QuizState`" invariant (no stdin injection).

The spike must answer, with evidence:
- Does Claude Code tolerate a multi-second blocking MCP tool call without killing it or the session? What is the practical timeout ceiling?
- Will the agent reliably *re-loop* across many cycles, or does it drift/stop? How robust is the prompt?
- Token/cost behavior of a long-lived idle-blocking turn.

**Decision gate:**
- **Go** → write `04-steering.md`, `05-drill.md`, `08-activity.md` against the long-poll design. The activity panel (8) becomes a natural piggyback (each `await_steer` return / tool call appends to the in-memory buffer).
- **No-go** → fall back to the honest minimal version: a chip writes its intent to `QuizState`, the browser shows "press Enter in your terminal to apply," and we descope the "kills the context-switch" promise. Document the fallback and move on.

**Spike result: GO** (2026-05-28, headless `claude -p` + sonnet — see `spike/wake/`). A blocking `await_steer` MCP tool drove a full browser-only steering loop: the host rendered once, then cycled `await_steer → act` four times (harder / skip / drill / STOP) with **no terminal input**, re-looping reliably and exiting on the STOP sentinel. The blocking primitive is unit-proven (`spike/wake/test_steer_bus.py`: block, wake, timeout, re-arm, FIFO, 50-cycle no-loss). Track B is cleared to proceed. *Caveat carried into the design docs:* on a deferred-MCP-tools host the agent must reach `ToolSearch` to discover the quiz tools — launch flags must not zero out built-ins (`--tools ""`). Remaining risks (MCP tool-timeout ceiling, loop durability over long sessions, token cost, clean shutdown) are listed in `spike/wake/README.md`.

### Sequencing

Do **not** write docs 01–10 up front. Write `01-anchors.md` and run the wake spike first — they are independent (anchors unblocks Track A; the spike unblocks Track B). Let the spike result decide whether the Track B docs get written at all.

## Invariants to preserve across all PRs

- Browser ↔ host only via `QuizState`. (Track B's `await_steer` reads/blocks on `QuizState`; it does **not** inject stdin.)
- **Built-in filesystem tools unchanged** — the host's Read/Grep/Glob confinement (`confine.py`) is untouched. New capability is added as **MCP tools**, a separate surface; adding MCP tools does not violate this.
- New MCP tools validate inputs and only mutate `QuizState` — no shell, no model judgment, no disk writes outside the snapshot path (`history.json`, written by the web/orchestrator process, is the documented exception).
- Frontend uses `textContent` only for any user/agent-supplied string (no `innerHTML`); diff-hunk rendering is DOM-built.
- Publish stays human-gated in the browser.
- Existing flow keeps working when all new features are ignored.
- Backwards-compat: old cached quiz JSON files (without new optional fields) must still load.

## Status

The original three-subagent dispatch refused the work as too large for one turn — a signal the brief was too coarse, now addressed by the two-track split.

Done:
- `01-anchors.md` written (Track A foundation).
- Host-wake spike run and **passed (GO)** — `spike/wake/` (blocking `await_steer` long-poll; deterministic primitive test + headless `claude -p` loop validation).

Doc filenames follow feature numbers (so `01-anchors.md` = feature #1). Next steps:
- **Implement PR A1 (anchors)** from `01-anchors.md` — it's the spine the rest of Track A builds on.
- Write the remaining Track A docs: `09-coverage-map.md` (PR A2), `03-feedback-loop.md` covering features 2/3/6 (PR A3), `07-card-view.md` (PR A4), `10-cross-pr-memory.md` (PR A5).
- Write the Track B docs against the validated long-poll design: `04-steering.md`, `05-drill.md`, `08-activity.md`.
