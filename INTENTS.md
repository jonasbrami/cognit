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

- **Engine is portable.** The quiz generator and grader live in a standalone module (`engine/`) with no GitHub API calls inside. The CLI commands are thin wrappers that call into the engine and handle GitHub-specific I/O at the edges. This keeps the door open to a v2 GitHub Action / GitHub App that reuses the same engine.
- **PR thread is the canonical state.** No external storage, no state branches, no workflow artifacts crossing runs. Everything that needs to persist is a PR comment.
- **One LLM, one provider in v1.** Multi-LLM orchestration is deferred (see Future vision).
- **No team-specific knowledge injection in v1.** Skills integration is deferred (see Future vision).
- **Local CLI is the only surface in v1.** GitHub Action auto-trigger was prototyped and then deliberately dropped — see Future vision.

## Architecture

One CLI command. The PR thread carries only the (opt-in) results comment — the quiz itself never touches it.

```
┌────────────────────────────────────┐
│  `cognit take` (CLI, local)         │
│                                    │
│  1. detect PR from current branch  │
│  2. cache hit?                     │
│     yes → load Quiz from           │ ──── reads ──► $TMPDIR/cognit/<sha1>.json
│             $TMPDIR/cognit/...      │ ◄─── writes ──
│     no  → fetch diff, call LLM,    │
│             write Quiz to cache    │
│  3. open browser UI                │
│  4. grade everything in-session    │
│     (det. + LLM open-Q grading)    │
│  5. show results inline            │
└─────────────┬──────────────────────┘
              │
              │ 6. user clicks "Publish results"
              │    (opt-in — nothing posted on submit)
              ▼
        POST /publish ──────────────────────────►  ┌──────────────────────────────┐
                                                   │  PR comment (results)        │
                                                   │  ─ total + per-Q score       │
                                                   │  ─ question prompts inlined  │
                                                   │  ─ your answers inlined      │
                                                   │  ─ open-Q LLM feedback       │
                                                   └──────────────────────────────┘
```

The PR thread carries **at most one comment** per take session, and only if the author chose to publish. The quiz itself lives in memory + an ephemeral local cache (`$TMPDIR/cognit/`) — no quiz comment, no answer key visible on the PR by default. The results comment is self-contained (question prompts + author answers inlined) so reviewers reading the PR don't need a separate quiz comment to cross-reference.

## Components

### `cognit take` CLI

- Auth: uses local `gh auth login` for PR I/O. LLM auth via Claude Code OAuth (`~/.claude/.credentials.json`) or `ANTHROPIC_API_KEY`. Anthropic is the only supported provider in v1.
- Invocation: `cognit take [--pr <url-or-number>] [--model <name>] [--min-diff-lines N] [--max-diff-lines N]`. Auto-detects PR from current branch.
- Steps:
  1. Detect the PR for the current branch via `gh pr view --json url`. (Skipped when `--pr` is passed.)
  2. **Get the Quiz** — local cache first, else generate fresh:
     - Cache path: `$TMPDIR/cognit/<sha1(pr_url)[:16]>.json`. If present, deserialize and skip to step 3.
     - Otherwise: fetch PR title, body via `gh pr view`; diff via `gh pr diff`; touched-file contents via `git show HEAD:<path>`. Skip if diff < `--min-diff-lines` (default 50), > `--max-diff-lines` (default 2000), or if PR body contains `quiz: skip`.
     - Call the LLM with a prompt that asks it to **decide both the count and the type-mix** based on diff size and complexity. A typo fix gets 2–3 probes; a 500-line refactor with new abstractions might warrant 8 or more. Question types: `mcq` (facts/invariants), `mermaid` (control or data flow — generated as 1 correct + 3 plausible-but-wrong variants in uniform style), `open` (LLM-graded against a rubric), `tf` (subtle behavioral claims).
     - Validate mermaid diagrams with `@mermaid-js/mermaid-cli` parse pass (skip silently if not installed); retry per-question on failure (max 2 retries); drop mermaid Q as last resort.
     - Post-process mermaid options to neutral A/B/C/D labels (prevents accidental answer leak from semantic labels like `correct`/`wrong_1`).
     - Write the Quiz JSON to the cache. **The quiz is NOT posted to the PR.**
  3. Spin up a local HTTP server (FastAPI + uvicorn, 127.0.0.1 only, random unused port), open the URL in the default browser. The Quiz is held in the server's closure.
  4. Browser renders the quiz with `mermaid.js` (real diagram rendering, not GitHub's markdown view), real form controls for MCQ, a textarea for the open question.
  5. On submit (POST `/submit`):
     - Cache the submitted `Answers` in the server's closure (so `/publish` can render an inlined results comment).
     - Grade MCQ + mermaid + T/F deterministically against the answer key.
     - LLM-grade the open question in-session.
     - Return the full `Results` JSON to the browser. **Nothing is posted to the PR yet.**
     - Browser renders the result panel inline: total score, per-question breakdown, open-question feedback in a blockquote.
  6. On clicking "Publish results to PR" (POST `/publish`):
     - Server renders a **self-contained results comment** via `render_results_inlined(quiz, answers, results)` — each question's prompt, the author's answer, the score, and any feedback are inlined.
     - Posts via `gh pr comment`. Confirms via status text in the UI.
     - Requires that `/submit` ran first in this session; otherwise responds 400.
- Stays alive until the user closes the browser tab or hits Ctrl-C.

**One command, one diagnostic.** `cognit generate` and `cognit grade` existed in earlier versions as separate CLI commands; both were collapsed into `take` once it became clear that the only happy-path use case is the author running a single command after opening their PR. The engine layer (`engine/generate.py`, `engine/grade.py`) is still standalone — a future GitHub App or webhook receiver can call into it without going through the CLI.

**No quiz on the PR thread.** Earlier versions posted the quiz as a PR comment with marker `<!-- cognit:quiz v1 -->` and used the thread as canonical state. After the collapse to one command, that storage stopped being load-bearing — and it carried real costs (answer key visible in plaintext, noise for reviewers, an extra artifact unrelated to code review). The in-memory + ephemeral-cache design keeps recovery working (close the tab, re-run, same quiz, no LLM re-bill) without putting anything on the PR until the author opts in to publish.

**Publishing is opt-in.** Solo devs can practice in private without leaving a trail; users who want a record click the button.

**Who can run `cognit take`:** anyone with `gh` access to the repo. The CLI doesn't gate by PR-author identity; downstream consumers (you, the human) decide who runs the local CLI.

## Quiz comment format

Markdown for humans, JSON code block for the CLI. The answer key is in plaintext on purpose — this is a voluntary self-quiz, and scrolling past your own answer key to cheat is a choice the author makes against their own learning.

```markdown
<!-- cognit:quiz v1 -->
## Quiz on your PR

Take it in your terminal: `cognit take` (or `cognit take <this PR URL>`).
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
| LLM provider | Anthropic only (Claude SDK). API key (`ANTHROPIC_API_KEY`) or Claude Code OAuth (`~/.claude/.credentials.json`) — auto-detected. |
| `--model` | `claude-sonnet-4-6` |
| `--min-diff-lines` | 50 (skip tiny PRs) |
| `--max-diff-lines` | 2000 (skip huge PRs) |
| `excludes` | `*-lock.*`, `*.lock`, `*.map`, `*.pb.*`, `*_pb2.py`, `*.generated.*`, `*.auto.*`, `dist/**`, `build/**` |
| question count | **LLM-decided.** Prompt instructs the model to pick the count and type-mix based on diff complexity. Typical range 2–10. |
| `context-strategy` | `diff + pr-body + touched-files-full` |

PR-level escape hatches: `quiz: skip` in PR description suppresses generation entirely.

## Error handling

| Failure | Behavior |
|---|---|
| Mermaid syntax invalid in any candidate | Retry per-question generation up to 2 times. If still invalid, drop the mermaid question. |
| Diff smaller than `--min-diff-lines` | `take` prints `"diff is N lines (< min) — skipping."` and exits zero. No PR comment. |
| Diff larger than `--max-diff-lines` | `take` prints `"diff is N lines (> max) — skipping."` and exits zero. No PR comment. |
| `quiz: skip` in PR body | `take` prints `"quiz: skip in PR body — skipping."` and exits zero. No PR comment. |
| LLM call fails (network, rate limit, validation) | CLI catches `AnthropicAPIError`/`ValidationError` and exits 1 with a friendly message. |
| `--show-results` with no results comment | Print `"no results comment found on this PR."` and exit 1. |

## Testing strategy

- **Unit tests** for the question generator: fixture diffs → assert valid JSON schema, valid mermaid, mermaid label neutralization.
- **Unit tests** for grading: fixture quiz JSON + fixture answers → assert correct deterministic + LLM-graded scoring.
- **Unit tests** for the LLM adapter using `respx` to mock `api.anthropic.com`. Covers both stages of generation (`generate_quiz_outline` and `generate_mermaid_set`) and `grade_open`, plus assertions on system-prompt + `cache_control` shape.
- **Unit tests** for the FastAPI server using `TestClient` (covers `/`, `/static/*`, `/submit`, `/publish`).
- **Playwright end-to-end** (manual, ad hoc): drive the browser through fill → submit → publish against the live PR; screenshot every state. The headless-Chrome `--screenshot` flag is a faster alternative for visual smoke.
- **CI** (`.github/workflows/ci.yml`): on every push, run `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`, and a CLI install smoke (`cognit --help` etc.).

## Non-goals (for v1)

- No merge blocking. No Check Runs. No branch protection integration. (Opt-in philosophy — the discipline is taking the quiz, not being forced through it.)
- **No GitHub Action auto-trigger.** Composite Actions wrapping `cognit generate --post` were prototyped end-to-end and dropped — not deferred. The collapse to a single `cognit take` command means there's no separate generation entrypoint to wrap. A future automation surface would call the engine layer directly (webhook receiver, GitHub App).
- No GitHub App / Marketplace listing. No hosted infrastructure. No SaaS.
- No multi-LLM orchestration. Single provider — Anthropic (Claude SDK).
- No team-specific knowledge injection (Skills). Single generic prompt for now.
- No team enforcement. No "did the author pass the quiz" reporting up to managers.
- No GitLab / Bitbucket support. GitHub only.
- No support for fork-PRs (`gh` operations require write access to the PR; non-author contributors can take the quiz locally but can't post results back to a PR they don't own).

## Future vision (v2 and beyond — explicitly deferred but preserved)

The Android-session vision points beyond v1. These are real product ambitions, not feature creep — captured here so we don't lose them while shipping a focused MVP.

### Fleet of LLMs
A generation orchestrator that fans out to multiple providers (OpenAI, Anthropic, Gemini, GitHub Models, local models), deduplicates similar questions, and picks a balanced set. **Why it matters:** diversity of perspectives, harder for authors to learn to pattern-match the questions, surfaces a wider range of comprehension gaps. **What it adds:** a generation orchestrator module in the engine, per-provider adapters, deduplication logic, more API keys to manage.

### Skills integration (team knowledge injection)
A `.cognit/skills/` directory of markdown files in the repo, loaded into the generation prompt. Teams describe their codebase's invariants, conventions, and architectural choices; the quiz generator uses them to ask questions that reflect the team's reality rather than generic code-comprehension probes. **Why it matters:** this is the real differentiator vs. Gater — questions that know what's idiomatic for *this* codebase, not what's idiomatic in general. **What it adds:** a Skills loader, prompt-engineering work to weave Skills into the generation context, possibly a `cognit skills validate` CLI command.

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
