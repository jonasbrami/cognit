# cognit — Roadmap

This document tracks deferred work and future directions. It is not a commitment — it reflects known opportunities, open investigations, and deliberate non-goals.

---

## Performance and latency

Wall-time is dominated by a single extended-thinking generation burst, not by diff size or network I/O. Measured baselines: Haiku ~120s, Sonnet ~320s end-to-end. The diff pipeline (fetch, filter, split) and grading are fast by comparison.

- **Stream the generation burst to the UI (`include_partial_messages=True`).** The `query()` loop currently consumes complete messages, so the activity feed goes quiet during the main generation turn. Forwarding `content_block_delta` stream events would give the user a heartbeat during the silence. Deferred — it adds complexity to the activity forwarder and the `/progress` poll interval caps the benefit; revisit if the feed feels frozen.
- **Thinking-budget A/B.** The extended-thinking budget is at its default. A lower budget trades quality for latency — worth an A/B once a post-explanation quality baseline exists.
- **Planner → parallel-drafters split.** Decode questions concurrently: a lightweight planner turn picks the question slots, then N drafter subagents render one question each in parallel. Highest-impact latency lever but a significant architectural change. Gate it on the plan-then-draft prompt restructure — if the explicit slot-plan step already cuts thinking thrash, the subagent split may not pay for its complexity.
- **Haiku default with Sonnet fallback.** With the hook-enforced correctness constraints, Haiku may be good enough for most PRs. A Haiku-default / Sonnet-fallback routing would cut median latency substantially.

## Grading

- **Batch open-question grading (`grade_open_batch`).** Today each open question is a separate `grade_open` call — one `claude` subprocess spawn per question, run serially. Collapse them into one `submit_grades` MCP tool returning `{grades: [{question_id, score, feedback}]}`, guarded by a coverage-check hook that denies if any expected `question_id` is missing. Designed but deferred as an independent change.

## Quiz quality

Post-answer explanations, the "lookup test", the usefulness checklist, the mermaid trigger, and option-distinctness validation have shipped. Follow-ons:

- **Difficulty / discrimination self-evaluation.** Have the agent score each drafted question on expected difficulty and discrimination and drop the weak ones before submitting. Measure first whether the prompt-side checklist alone raises quality enough.
- **Per-distractor analytics.** Track which MCQ / mermaid distractors authors pick most — high-pick wrong answers signal a real misconception worth surfacing. Requires opt-in result persistence, which breaks the current ephemeral design; tie to a future hosted tier.

## Future surfaces

- **GitHub App.** A Marketplace-installable app wrapping the same engine via a webhook receiver (hosted backend, OAuth identity, hosted SPA reusing the existing JS). The engine is already surface-agnostic; this is plumbing, not a rewrite. Not on the v1 roadmap.
- **Fleet of LLMs.** Fan generation out to multiple providers, deduplicate similar questions, and pick a balanced set — diversity of perspective, harder to pattern-match.
- **Skills integration.** A `.cognit/skills/` directory of markdown describing a codebase's invariants and conventions, loaded into the generation prompt so questions reflect the team's reality. The real differentiator versus generic code-comprehension tools.
- **IDE integration.** Quiz inline in VS Code / JetBrains instead of a browser.
- **Reviewer-side mode.** Quiz the reviewer, not just the author — same engine, different audience. Lower priority; the author-side loop is the differentiated bet.

## Developer experience

- **CLI streaming feedback.** `cognit take` prints a bare spinner during generation. Forward the activity feed (tool calls, thinking summaries, validation events) to the terminal so long runs aren't opaque.
- **Playwright in CI.** The UI integration tests (`tests/server/test_ui_flow.py`) need a browser installed. Gate them behind a marker and document the CI setup so they run reliably.
- **Cache invalidation.** The quiz cache lives at `$TMPDIR/cognit/<digest>.json` and is cleared by hand today. A `cognit take --force` (or `cognit regenerate`) would be cleaner.
