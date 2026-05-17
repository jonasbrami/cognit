# PR Author Quiz — Design

A voluntary tool that quizzes the **PR author** on their own pull request to surface the gap between what they think the code does and what it actually does — before they merge.

## The problem

Developers increasingly rely on AI tools to write and review code, which creates a comprehension gap — code gets merged that nobody on the team fully understands. The risk isn't bad code per se; it's **false confidence in code that looks reasonable but does something subtly different from what the developer expects**.

## The philosophy

- **Opt-in, not enforced.** Like CI checks, linters, or pre-commit hooks. Developers choose to enable it because it makes them better and protects them from their own blind spots. It assumes good intent.
- **Failing the quiz doesn't block the merge.** The author can ignore it. The goal is to surface the gap between their mental model and the code's actual behavior — so they don't merge code that doesn't match what they think it does.
- **The quiz is the diagnostic; the explanation is the medicine.** The "aha" moment when a developer answers wrong and realizes the code does something they didn't expect is the entire point.
- **North star: maximize the utility of human attention.** Let LLMs do the heavy lifting of probing understanding so the limited human time spent on a PR is spent on what genuinely needs a human mind.

## Why this exists (vs. what's out there)

- Existing comparable tools (`dkamm/pr-quiz`, Gater) target the **reviewer**, not the author. Reviewer-side gating is downstream of the real problem: people open PRs they don't fully understand, especially when AI wrote most of the code.
- A teaching loop is more valuable than a pass/fail gate.
- Voluntary use removes the entire blocking/override/branch-protection ceremony. The author opts in by running the CLI. If they don't, the PR still merges — the cost of skipping is forgone learning, not a procedural roadblock.

## Design principles for the MVP

- **Engine is portable.** The quiz generator and grader live in a standalone module (`internal/engine/`) with no GitHub API calls inside. The Action steps and the CLI are thin wrappers that call into the engine and handle GitHub-specific I/O at the edges. This keeps the door open to a v2 GitHub App that reuses the same engine.
- **PR thread is the canonical state.** No external storage, no state branches, no workflow artifacts crossing runs. Everything that needs to persist is a PR comment.
- **One LLM, one provider in v1.** Multi-LLM orchestration is deferred (see Future vision).
- **No team-specific knowledge injection in v1.** Skills integration is deferred (see Future vision).

## Architecture

Three pieces, one PR thread:

```
┌──────────────────────────┐
│  GitHub Action           │     1. PR opened
│  (generator)             │ ──────────────────►  posts quiz comment
│  triggered on PR open    │                      (markdown + JSON state)
└──────────────────────────┘
                                                          │
                                                          ▼
                                              ┌────────────────────────┐
                                              │  PR comment (the quiz) │
                                              │  ─ rendered questions  │
                                              │  ─ mermaid native      │
                                              │  ─ JSON state block    │
                                              └────────────────────────┘
                                                          │
                            2. author runs `quizz take`   │
                                                          ▼
┌──────────────────────────┐                ┌────────────────────────┐
│  Local CLI               │                │  PR (via gh CLI)       │
│  ─ reads quiz comment    │ ◄────────────► │  read & write          │
│  ─ opens browser UI      │                │  comments              │
│  ─ grades deterministic  │                └────────────────────────┘
│  ─ posts answers comment │                            │
└──────────────────────────┘                            │
                                                        ▼
                                              ┌────────────────────────┐
                                              │  PR comment (answers)  │
                                              │  ─ user's responses    │
                                              │  ─ deterministic score │
                                              └────────────────────────┘
                                                        │
                                                        ▼
┌──────────────────────────┐
│  GitHub Action           │     3. on issue_comment
│  (grader)                │ ──────────────►  grades open question
│  triggered on answers    │                  posts results comment
└──────────────────────────┘
```

Everything that needs to be persisted is a PR comment. No state branches, no workflow artifacts crossing runs, no external storage.

## Components

### Generator Action (`.github/workflows/quizz-generate.yml`)

- Trigger: `pull_request` on `opened` and `synchronize` (skip if diff <50 lines or >2000 lines; skip if PR body contains `quiz: skip`).
- Steps:
  1. Checkout repo (sparse, just changed files).
  2. Fetch PR title, body, full content of touched files.
  3. Call GitHub Models (free, `permissions: models: read`) with a structured-output prompt that returns 5 questions: 2 MCQ + 1 mermaid (reference + 3 distractors, all 4 in uniform style) + 1 open question + 1 true/false.
  4. Validate mermaid diagrams with `@mermaid-js/mermaid-cli` parse pass; retry on failure (max 2 retries).
  5. Render the markdown comment and post it to the PR via `gh pr comment`.
- Output comment shape: see [Quiz comment format](#quiz-comment-format).

### CLI (`quizz take`)

- Auth: uses local `gh auth login`. No additional tokens.
- Invocation: `quizz take` (auto-detects PR from current branch) or `quizz take <PR-URL>`.
- Steps:
  1. Find the latest `<!-- quizz:quiz v1 -->` comment in the target PR via `gh pr view --json comments`.
  2. Parse the embedded JSON state.
  3. Spin up a local HTTP server on an unused port, open `http://localhost:<port>` in the default browser.
  4. Browser renders the quiz with `mermaid.js` (real diagram rendering, not GitHub's), real form controls for MCQ, a textarea for the open question.
  5. On submit:
     - Grade MCQ + mermaid + true/false deterministically against the answer key (which is in the JSON state, in plaintext — voluntary system).
     - Show MCQ/mermaid/T/F score in browser immediately.
     - Post an answers comment to the PR via `gh pr comment` with the user's responses + the deterministic score.
     - Wait (with polling) for the grader Action to reply, then show the open-question score and explanations.
- Stays alive until user closes the browser tab or hits Ctrl-C.
- Distribution: single Go binary (`go install`-able + Homebrew tap + a downloads page).

**Who can run `quizz take`:** anyone with `gh` access to the repo. The CLI doesn't gate by PR-author identity, because the grader Action already does — answers from non-author commenters are silently ignored. This means a reviewer or teammate can post answers on someone else's PR (they'll appear as a comment in the thread), but no results comment will follow. Not a security issue, just a UX detail to call out in docs.

### Grader Action (`.github/workflows/quizz-grade.yml`)

- Trigger: `issue_comment` (created) where the comment matches `<!-- quizz:answers v1 -->`.
- Guard: comment author must equal PR author (`github.event.comment.user.login == github.event.issue.user.login`). Otherwise no-op.
- Steps:
  1. Parse the answers comment.
  2. Locate the corresponding quiz comment (most recent `<!-- quizz:quiz v1 -->` from the bot).
  3. Call GitHub Models with: question, user's answer, rubric → returns score 0–100 and a short explanation.
  4. Post a results comment with `<!-- quizz:results v1 -->` marker: total score, per-question breakdown, the open-question feedback, the right answer for anything the user got wrong.

## Quiz comment format

Markdown for humans, JSON code block for the CLI. The answer key is in plaintext on purpose — this is a voluntary self-quiz, and scrolling past your own answer key to cheat is a choice the author makes against their own learning.

```markdown
<!-- quizz:quiz v1 -->
## Quiz on your PR (5 questions)

Take it in your terminal: `quizz take` (or `quizz take <this PR URL>`).
Or scroll down and answer in your head — see what you got wrong at the bottom.

### Question 1 — Multiple choice
Which assertion best describes the new caching strategy?
- A) Per-request, in-memory, no eviction
- B) Per-user, Redis-backed, TTL 5min
- C) Per-tenant, in-memory, LRU 1000 entries
- D) Per-request, Redis-backed, no TTL

### Question 2 — Pick the matching diagram
Which mermaid diagram best represents the new auth flow?

#### Option A
\`\`\`mermaid
flowchart LR
  Client --> Gateway
  Gateway --> Auth
  Auth --> DB
\`\`\`

#### Option B
\`\`\`mermaid
flowchart LR
  Client --> Auth
  Auth --> Gateway
  Gateway --> DB
\`\`\`

... (Options C and D)

### Question 3 — Open
Explain why you chose `RWMutex` over `Mutex` in `cache.go:42`.

(continue for questions 4 and 5)

---
<details>
<summary>Quiz state (used by the CLI — don't edit)</summary>

\`\`\`json
{
  "version": "1",
  "pr_number": 42,
  "questions": [
    {"id": "q1", "type": "mcq", "answer": "C", "options": ["A","B","C","D"]},
    {"id": "q2", "type": "mermaid", "answer": "B", "options": ["A","B","C","D"]},
    {"id": "q3", "type": "open", "rubric": "Must mention concurrent reads, write contention, ..."},
    {"id": "q4", "type": "tf", "answer": true},
    {"id": "q5", "type": "mcq", "answer": "B", "options": ["A","B","C","D"]}
  ]
}
\`\`\`
</details>
```

## Configuration

Single workflow input file with sensible defaults. Tunable knobs:

| Knob | Default |
|---|---|
| `llm-model` | `gpt-4o-mini` (via GitHub Models) |
| `min-diff-lines` | 50 |
| `max-diff-lines` | 2000 |
| `excludes` | `*-lock.*`, `*.lock`, `*.map`, `*.pb.*`, `*_pb2.py`, `*.generated.*`, `*.auto.*`, `dist/**`, `build/**` |
| `question-mix` | `2 mcq, 1 mermaid, 1 open, 1 tf` |
| `context-strategy` | `diff + pr-body + touched-files-full` |
| `regen-on-sync-threshold` | 20% (regenerate when the changed-line set differs by more than this fraction from the previous quiz's diff) |

PR-level escape hatches: `quiz: skip` in PR description suppresses generation entirely.

## Error handling

| Failure | Behavior |
|---|---|
| LLM call fails | Retry once with exponential backoff. If still fails, post a comment "Quiz generation failed: <err>. No retry needed — push to retrigger." Exit zero (PR is not blocked anyway). |
| Mermaid syntax invalid in any candidate | Retry generation up to 2 times. If still invalid, drop the mermaid question, generate one additional MCQ. |
| Diff too large | Skip generation, post comment "PR too large for auto-quiz; run `quizz take --diff-only` locally for a lean version." |
| CLI can't find quiz comment | Print "No quiz found on this PR — has the Action run yet?" with a hint to check workflow status. |
| CLI grading: open question grader times out | Post answers comment, exit; results comment will arrive when grader completes. CLI users can re-run `quizz take --show-results` to fetch later. |
| Grader Action runs but quiz comment is gone | Log + skip, no result comment. |
| Listener fires on someone else's comment | Hard guard on `comment.user.login == issue.user.login`; otherwise exit zero immediately. |

## Testing strategy

- **Unit tests** for the question generator: fixture diffs (small/medium/large/binary-heavy) → assert valid JSON schema, valid mermaid for each candidate, correct question mix.
- **Unit tests** for the CLI grader: fixture quiz JSON + fixture answers → assert correct deterministic scoring.
- **Integration test** via `act` (run GitHub Actions locally): apply a fixture PR diff, run the generator, assert the comment is posted with the right shape.
- **End-to-end smoke test** in a sandbox repo, manual: open a PR, watch quiz comment appear, run `quizz take`, take quiz, watch results comment appear.
- **CI for the Action itself** (in this repo): on every push, run all unit tests, build the CLI, lint the workflows, run `act` against fixture PRs.

## Non-goals (for v1)

- No merge blocking. No Check Runs. No branch protection integration. No override mechanism. (Opt-in philosophy — the discipline is taking the quiz, not being forced through it.)
- No GitHub App / Marketplace listing. No hosted infrastructure. No SaaS.
- No multi-LLM orchestration. Single configurable provider, default GitHub Models.
- No team-specific knowledge injection (Skills). Single generic prompt for now.
- No team enforcement. No "did the author pass the quiz" reporting up to managers.
- No CLI-only mode (`quizz generate`). Quizzes always come from the Action.
- No GitLab / Bitbucket support. GitHub only.
- No support for fork-PRs in the initial cut (the generator Action's `GITHUB_TOKEN` is read-only on forks).

## Future vision (v2 and beyond — explicitly deferred but preserved)

The Android-session vision points beyond v1. These are real product ambitions, not feature creep — captured here so we don't lose them while shipping a focused MVP.

### Fleet of LLMs
A generation orchestrator that fans out to multiple providers (OpenAI, Anthropic, Gemini, GitHub Models, local models), deduplicates similar questions, and picks a balanced set. **Why it matters:** diversity of perspectives, harder for authors to learn to pattern-match the questions, surfaces a wider range of comprehension gaps. **What it adds:** a generation orchestrator module in the engine, per-provider adapters, deduplication logic, more API keys to manage.

### Skills integration (team knowledge injection)
A `.quizz/skills/` directory of markdown files in the repo, loaded into the generation prompt. Teams describe their codebase's invariants, conventions, and architectural choices; the quiz generator uses them to ask questions that reflect the team's reality rather than generic code-comprehension probes. **Why it matters:** this is the real differentiator vs. Gater — questions that know what's idiomatic for *this* codebase, not what's idiomatic in general. **What it adds:** a Skills loader, prompt-engineering work to weave Skills into the generation context, possibly a `quizz skills validate` CLI command.

### GitHub App graduation
A Marketplace-installable GitHub App that wraps the same engine. Webhook receiver, hosted backend (Cloudflare Workers + D1 most likely), OAuth user identity, hosted SPA quiz UI reusing the same JS as the local CLI's browser view. **Why it matters:** teams that don't want a workflow file in every repo, or want centralized config across many repos. **What it adds:** ~3–4 weeks of plumbing (webhook handler, OAuth flow, DB schema, hosting, Marketplace listing). The engine itself stays the same — that's the whole point of the v1 design principles.

### Other future possibilities (not committed, just named)
- **Richer question types:** sequence-of-events ordering, "what does this return?" with input fixtures, design-intent questions tagged separately from generic open questions.
- **Learning history:** opt-in record of which kinds of questions a developer tends to miss, so quizzes adapt over time (would require persistence, breaks the "ephemeral" rule — would graduate to the GitHub App).
- **Reviewer-side mode:** the original Gater / dkamm framing — quizzing the reviewer too. Different audience, but the same engine can serve it.
- **IDE integration:** quiz appears inline in VSCode / JetBrains rather than in a browser. Cool but a long way from MVP.

## Open items

- **CLI distribution.** Go binary via `go install`, Homebrew, or a GitHub-hosted releases page? Probably all three eventually, but the MVP picks one — likely `go install` for simplicity, given the audience is developers who already have a Go toolchain or are willing to install one.
- **Mermaid distractor quality.** The "all four in uniform style" prompt may still leak the answer through subtle cues (LLMs often draw the "right" one more confidently). May need an explicit style-spec in the prompt and/or a post-hoc rewrite pass to normalize.
- **Open-question rubric quality.** Generated rubrics are only as good as the LLM's understanding of the diff. Rubric-quality regression tests on a curated set of diffs are probably needed.
- **`act` coverage for `issue_comment`-triggered workflows.** `act` supports it but the testing ergonomics aren't as clean as `pull_request` triggers. Document a local workflow for testing the grader.

---

## Research appendix

### Competitive landscape (May 2026)

Two existing tools cover adjacent ground; neither does what we're building:

- **`dkamm/pr-quiz`** — open source GitHub Action, MIT, 208 stars. Targets the *reviewer*, MCQ only, blocks merge via tunneled web UI (ngrok). Single human commit (June 2025), v0.1.0 release (July 2025), only dependabot activity since. Effectively dormant.
- **Gater (`usegater.app`)** — closed-source commercial. Targets the *reviewer*. Quiz lives in a Chrome extension over the GitHub PR page. Free personal tier, $20/mo Pro for 5/10/15 seats. Active marketing, no public technical blog.

Both quiz the reviewer of AI-generated code. **We quiz the author of any code, including their own.** No competing tool uses mermaid-diagram-selection as a question type.

### Feasibility study summary (May 2026)

Three architectures were investigated in parallel by independent agents:

| | A — Inline PR | B — GH Pages | C — GitHub App + UI |
|---|---|---|---|
| Verdict | YELLOW | YELLOW | GREEN |
| Effort | ~1–2 wks | ~3–4× A | ~6 wks |
| External infra | None | Tiny proxy needed | Full backend |

All three are buildable; B is dominated (more work than A, worse UX than C). C is the SaaS shape that Gater already occupies — we chose not to compete there.

**The architecture above is none of A/B/C** — it's a hybrid that emerged after the user pointed out the whole tool can be voluntary. By dropping the merge-blocking ceremony, we drop the need for Check Runs, branch protection, overrides, and the state-across-workflows complexity that made A awkward. What's left is two thin Actions plus a CLI that uses `gh` for auth and posting. No external hosting, no auth complexity, no SaaS.
