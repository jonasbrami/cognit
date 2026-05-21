# quizz

> Voluntary, opt-in PR-author comprehension quizzes. Surface the gap between what you think your code does and what it actually does — before you merge.

## What this is

A local CLI that quizzes the **author** of a pull request (not the reviewer) on the code they're about to merge. One command:

- `quizz take` — auto-detects the PR for the current branch, generates a quiz from the diff if none exists yet (calls an LLM, posts the quiz as a PR comment), opens the quiz in your local browser, grades everything in-session, and lets you optionally publish the results back to the PR.

Like CI checks, linters, or pre-commit hooks: opt-in. Failing doesn't block merge — the value is the "aha" moment when you realize the code does something you didn't expect.

> v1 ships as a local CLI only. The GitHub Actions wrapper that would auto-trigger the quiz on PR open is **not on the roadmap** — see `INTENTS.md`.

## How it works

1. You open a PR.
2. From a checkout of the PR branch, run `quizz take`. The CLI:
   - Auto-detects the PR for the current branch via `gh`.
   - If no quiz comment exists yet on the PR: reads the diff, calls an LLM (Claude via the Anthropic SDK; Claude Code OAuth or `ANTHROPIC_API_KEY`), and posts a structured quiz comment to the PR. The LLM picks question count and type-mix based on diff complexity (typically 2–10 questions across MCQ, mermaid-pick, open, true/false).
   - Opens `localhost` in your browser with a polished form (mermaid diagrams rendered client-side).
3. You answer, hit Submit. Everything is graded in-session: MCQ / mermaid / TF deterministically; the open question is LLM-graded against a rubric the generator wrote.
4. You see results inline in the browser. If you want a record on the PR, click **Publish results to PR**. If you don't, nothing is posted.

## Quickstart

### 1. Install the CLI

```bash
pipx install quizz
# or
uv tool install quizz
```

(Until v0.1.0 is on PyPI, install from this repo: `uv tool install --from <path-or-git-url> quizz`.)

### 2. Authenticate

Either:

- **Claude Code OAuth** (recommended, zero config): if you have `claude` CLI installed and have run `claude login`, the adapter automatically uses your `~/.claude/.credentials.json` token. Billed to your Claude Code subscription.
- **API key**: `export ANTHROPIC_API_KEY=sk-ant-...`

Also make sure you have:

- `gh` CLI installed and authenticated (`gh auth login`)
- For mermaid validation: `npm install -g @mermaid-js/mermaid-cli@10` (optional locally — the validator skips silently if missing; only required when you want strict validation)

### 3. Use it

```bash
# from a checkout of your PR branch
quizz take
# answer in browser, submit, optionally publish
```

## Configuration

`quizz take` accepts:

| Flag | Default | Description |
|---|---|---|
| `--pr` | (auto-detect from current branch) | PR URL or number. |
| `--model` | `claude-sonnet-4-6` | Anthropic model name. |
| `--min-diff-lines` | 50 | Skip auto-generation if the diff is smaller than this. |
| `--max-diff-lines` | 2000 | Skip auto-generation if the diff is larger than this. |
| `--show-results` | (off) | Print the latest results comment as JSON instead of opening the browser. |

To suppress quiz generation on a specific PR, include `quiz: skip` in the PR description.

## Rate limits

- **Claude Code OAuth path**: bound by your Claude Code subscription limits (per-model RPM/daily).
- **API key path**: standard Anthropic API limits.

Anthropic is the only supported provider in v1.

## Status

v1.0 ships:
- A single CLI command: `quizz take`. Generates the quiz on first run, opens the browser, grades in-session, opt-in publish.
- 4 question types (MCQ, mermaid-pick with auto-neutralized A/B/C/D labels, open, true/false).
- Anthropic adapter via tool use (guaranteed-schema output) with Claude Code OAuth fallback.
- Local FastAPI server with embedded HTML/JS/CSS + `mermaid.js` UMD bundle.

Future (see [`INTENTS.md`](INTENTS.md)):
- GitHub App (no per-repo workflow file).
- Fleet of LLMs for question diversity.
- Skills integration (team domain knowledge in generation prompts).
- IDE integration.

## Why this exists

There's a name for the problem this tool exists to address: **comprehension debt**. As Addy Osmani puts it:

> Comprehension debt is the growing gap between how much code exists in your system and how much of it any human being genuinely understands. Unlike technical debt, which announces itself through mounting friction […] comprehension debt breeds false confidence.[^1]

The risk isn't bad code per se; it's confidence in code that looks reasonable but does something subtly different from what the author thinks. AI accelerates this mechanically — in Anthropic's own skill-formation study, "the AI group averaged 50% on the quiz, compared to 67% in the hand-coding group."[^2] Simon Willison describes the same drift from the inside: "I no longer have a firm mental model of what they can do and how they work, which means each additional feature becomes harder to reason about."[^3] Margaret-Anne Storey traces this further back to teams losing the *theory* of their own system — by week seven of one project she studied, "no one on the team could explain *why* certain design decisions had been made or *how* different parts of the system were supposed to work together."[^4]

Anyone shipping with AI has been there: you "review" a diff in ten minutes, nod through code that *looks* right, then realize a week later you can't explain why a particular line is there. **Reviewing LLM-generated code properly — actually understanding it, not just skimming — costs about as much time as writing it yourself.** Most of us skip that cost and pay the interest later.

And skipping the cost doesn't remove the responsibility. **The code — not the prompt, not the intent — is what runs in production.** Computers execute code; they don't read your prompt, and they don't read your mind. Humans, not models, are responsible for the code they ship.

We've all felt this outside software too. You think you understand a topic — until the exam asks you something specific, and the gap shows up the moment you reach for the answer. **You only really learn it by being tested on it.**

That's what `quizz` does, for code you're about to merge. The quiz is the diagnostic; the explanation of the right answer is the medicine. Human attention is precious — the north star is to use LLMs to *illuminate areas of non-comprehension* so the time you spend reading your own PR lands on what actually needs a human mind.

**It's the inverse of the usual LLM coding flow.**

> Coding with AI: *human writes prompt → LLM writes code.*
> CDD: *LLM reads code → LLM writes prompts the human answers.*

Same model, arrows flipped — and the loop closes on the only question that matters: does the code do what you *intended* it to do? CDD is intent alignment for humans, run by the same machinery that wrote the code in the first place.

Call this **comprehension-driven development (CDD)**: a change isn't done until the author has been examined on it. Each question you grapple with — especially the ones you get wrong — is **comprehension credit** banked against the same debt Osmani names. The LLM is the examiner; the human stays in the loop where it matters: building the mental model.

*(Future: the author picks which areas of the diff to be examined on and at what depth — not in v1.)*

[^1]: Addy Osmani, ["Comprehension Debt"](https://addyosmani.com/blog/comprehension-debt/).
[^2]: Anthropic, ["How AI Impacts Skill Formation"](https://www.anthropic.com/research/AI-assistance-coding-skills).
[^3]: Simon Willison, ["Cognitive debt"](https://simonwillison.net/2026/Feb/15/cognitive-debt/).
[^4]: Margaret-Anne Storey, ["Cognitive Debt"](https://margaretstorey.com/blog/2026/02/09/cognitive-debt/).

## License

MIT
