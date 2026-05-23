# cognit

> Voluntary, opt-in PR-author comprehension quizzes. Surface the gap between what you think your code does and what it actually does — before you merge.

![Quiz UI: four questions (MCQ, mermaid-pick, open, true/false) generated from a PR diff, with a sidebar progress tracker.](docs/img/cognit-questions.png)

A local CLI that quizzes the **author** of a pull request (not the reviewer) on the code they're about to merge. One command, runs locally, results stay on your machine unless you explicitly publish them to the PR.

## TL;DR

- `cognit take` — auto-detects the PR for your current branch, generates a quiz from the diff via Claude, opens it in your browser, grades in-session.
- **Nothing is posted to GitHub** unless you click **Publish results to PR**. The quiz itself is never published; only a results comment, only if you ask.
- Like CI checks or pre-commit hooks: opt-in. Failing doesn't gate anything — the value is the "aha" when you realize the code does something you didn't expect.
- Anthropic-only in v1. Uses your Claude Code OAuth session if you have one, or `ANTHROPIC_API_KEY`.

## Quickstart

### Prerequisites

| Tool | Required? | Why |
|---|---|---|
| Python **≥3.12** | required | runtime |
| [`gh`](https://cli.github.com/) (logged in via `gh auth login`) | required | PR detection, diff fetch, comment publish |
| `git` | required | reads files at HEAD for context |
| A web browser | required | the quiz UI runs at `http://127.0.0.1:<random-port>` |
| [`claude`](https://docs.claude.com/en/docs/claude-code/overview) CLI (logged in via `claude login`) | optional | enables the OAuth auth path so you don't need an API key |
| [`@mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli) (`mmdc`) | optional | fastest path for server-side mermaid validation. If absent, `cognit` falls back to a lazily-built Docker parse-only image, then to a Python regex backstop — see [Mermaid validation](#mermaid-validation). |

### Install

```bash
# pick one:
uv tool install cognit
pipx install cognit
```

> Want the latest unreleased changes? Install from source instead:
> `uv tool install git+https://github.com/jonasbrami/cognit.git`

### Authenticate

Either path works — `cognit` auto-detects:

- **Claude Code OAuth (recommended, zero config).** If you've run `claude login`, the adapter reads `~/.claude/.credentials.json` automatically. Billed to your Claude Code subscription.
- **API key.** `export ANTHROPIC_API_KEY=sk-ant-...`

### Run it

```bash
# from a checkout of your PR branch:
cognit take
```

That's it. The CLI:

1. Detects the PR for the current branch via `gh`.
2. Generates a quiz from the diff (or loads it from the local cache at `$TMPDIR/cognit/` if you've already generated one for this PR).
3. Opens your browser to the quiz.
4. Grades everything in-session when you hit Submit — MCQ / mermaid / true-false deterministically; open questions are LLM-graded against a rubric the generator wrote.
5. Shows you results. Click **Publish results to PR** if you want a record on GitHub; otherwise nothing leaves your laptop.

![Results view: per-question scores, total, and the Discard / Publish-to-PR controls.](docs/img/cognit-results.png)

## How it works

```mermaid
sequenceDiagram
  actor User
  participant CLI as cognit take
  participant gh as gh CLI
  participant LLM as Anthropic
  participant Web as Local browser

  User->>CLI: cognit take
  CLI->>gh: pr view / pr diff
  gh-->>CLI: title, body, diff, files
  CLI->>LLM: outline call
  LLM-->>CLI: QuizOutline + mermaid specs
  loop per mermaid placeholder
    CLI->>LLM: artisan call
    LLM-->>CLI: 4 diagrams + correct key
  end
  CLI->>Web: serve quiz (inline JSON)
  User->>Web: answer + submit
  Web->>CLI: POST /submit
  CLI->>LLM: grade_open per open Q
  LLM-->>CLI: score + feedback
  CLI-->>Web: Results
  opt user clicks Publish
    Web->>CLI: POST /publish
    CLI->>gh: post results comment
  end
```

The LLM picks question count and type mix based on diff complexity — typically 2–10 questions across MCQ, mermaid-pick, open, and true/false. To suppress quiz generation on a specific PR, include `quiz: skip` in the PR description.

> v1 ships as a local CLI only. A GitHub Actions wrapper that would auto-trigger the quiz on PR open is **not on the roadmap** — see [`INTENTS.md`](INTENTS.md).

## Configuration

```bash
cognit take [--pr URL] [--model NAME] [--min-diff-lines N] [--max-diff-lines N] [--show-results]
```

| Flag | Default | Description |
|---|---|---|
| `--pr` | auto-detect from current branch | PR URL or number. |
| `--model` | `claude-sonnet-4-6` | Anthropic model name. |
| `--min-diff-lines` | 50 | Skip auto-generation if the diff is smaller than this. |
| `--max-diff-lines` | 2000 | Skip auto-generation if the diff is larger than this. |
| `--show-results` | off | Print the latest results comment as JSON instead of opening the browser. |

**Rate limits** follow whichever auth path you're using: Claude Code subscription limits for OAuth, standard Anthropic API limits for API keys.

## Mermaid validation

The generator produces mermaid-pick questions (four diagrams, one correct). Before a quiz is served, every diagram is validated server-side so a malformed diagram never reaches the browser. `cognit` tries three layers, in order of preference:

1. **`mmdc`** (`npm install -g @mermaid-js/mermaid-cli`) — fastest, no per-call overhead.
2. **Docker** — if `mmdc` is absent but `docker` is available, `cognit` lazily builds a small parse-only validator image on first use (no Chromium; just `mermaid` + `jsdom`).
3. **Python regex backstop** — if neither is present, a lightweight check still runs, catching the most common LLM failure modes (missing diagram header, grossly unbalanced brackets, `[/text]` parallelogram traps).

To trace which layer is chosen (and other internal decisions), set `COGNIT_LOG_LEVEL=DEBUG`:

```bash
COGNIT_LOG_LEVEL=DEBUG cognit take
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: no PR detected from current branch` | Push the branch and open a PR, or pass `--pr <url>`. |
| `diff is N lines (< 50) — skipping` | Lower the floor: `cognit take --min-diff-lines 0`. |
| `diff is N lines (> 2000) — skipping` | Raise the ceiling: `cognit take --max-diff-lines 5000`. Long diffs cost more and the LLM may struggle to pick good questions. |
| `Your Claude Code OAuth session is expired` | `claude login` to refresh, or `export ANTHROPIC_API_KEY=...` to switch to an API key. |
| `Anthropic provider needs credentials` | Either `export ANTHROPIC_API_KEY=sk-ant-...` or `claude login`. |
| `gh` errors out | `gh auth status` to check, `gh auth login` to (re-)authenticate. |
| Browser doesn't open / port collision | The CLI picks a random free port and `webbrowser.open`s it. If your environment is headless, copy the `http://127.0.0.1:<port>` URL from the CLI output. |
| Want to regenerate after a cached quiz | The cache lives at `$TMPDIR/cognit/<digest>.json` — delete that file and run `cognit take` again. |

## Why this exists

There's a name for the problem this tool exists to address: **comprehension debt**. As Addy Osmani puts it:

> Comprehension debt is the growing gap between how much code exists in your system and how much of it any human being genuinely understands. Unlike technical debt, which announces itself through mounting friction […] comprehension debt breeds false confidence.[^1]

The risk isn't bad code per se; it's confidence in code that looks reasonable but does something subtly different from what the author thinks. AI accelerates this mechanically — in Anthropic's own skill-formation study, "the AI group averaged 50% on the quiz, compared to 67% in the hand-coding group."[^2] Simon Willison describes the same drift from the inside: "I no longer have a firm mental model of what they can do and how they work, which means each additional feature becomes harder to reason about."[^3] Margaret-Anne Storey traces this further back to teams losing the *theory* of their own system — by week seven of one project she studied, "no one on the team could explain *why* certain design decisions had been made or *how* different parts of the system were supposed to work together."[^4]

Anyone shipping with AI has been there: you "review" a diff in ten minutes, nod through code that *looks* right, then realize a week later you can't explain why a particular line is there. **Reviewing LLM-generated code properly — actually understanding it, not just skimming — costs about as much time as writing it yourself.** Most of us skip that cost and pay the interest later. And skipping it doesn't remove the responsibility: **the code, not the prompt, is what runs in production.** Computers don't read your mind; humans, not models, are responsible for what they ship.

We've all felt this outside software too. You think you understand a topic — until the exam asks you something specific, and the gap shows up the moment you reach for the answer. **You only really learn it by being tested on it.**

That's what `cognit` does, for code you're about to merge. The quiz is the diagnostic; the explanation of the right answer is the medicine. Human attention is precious — the north star is to use LLMs to *illuminate areas of non-comprehension* so the time you spend reading your own PR lands on what actually needs a human mind.

**It's the inverse of the usual LLM coding flow.**

> Coding with AI: *human writes prompt → LLM writes code.*
> CDD: *LLM reads code → LLM writes prompts the human answers.*

Same model, arrows flipped — and the loop closes on the only question that matters: does the code do what you *intended* it to do? CDD is intent alignment for humans, run by the same machinery that wrote the code in the first place.

Call this **comprehension-driven development (CDD)**: a change isn't done until the author has been examined on it. Each question you grapple with — especially the ones you get wrong — is **comprehension credit** banked against the same debt Osmani names. The LLM is the examiner; the human stays in the loop where it matters: building the mental model.

*(Future: the author picks which areas of the diff to be examined on and at what depth — not in v1.)*

## Status

**v1.0 ships:**

- A single CLI command: `cognit take`. Generates the quiz on first run, opens the browser, grades in-session, opt-in publish.
- 4 question types (MCQ, mermaid-pick with auto-neutralized A/B/C/D labels, open, true/false).
- Anthropic adapter via tool use (guaranteed-schema output) with Claude Code OAuth fallback.
- Local FastAPI server with embedded HTML/JS/CSS + a vendored `mermaid.js` UMD bundle (no CDN at runtime).

**Future** (see [`INTENTS.md`](INTENTS.md)):

- GitHub App (no per-repo workflow file).
- Fleet of LLMs for question diversity.
- Skills integration (team domain knowledge in generation prompts).
- IDE integration.

[^1]: Addy Osmani, ["Comprehension Debt"](https://addyosmani.com/blog/comprehension-debt/).
[^2]: Anthropic, ["How AI Impacts Skill Formation"](https://www.anthropic.com/research/AI-assistance-coding-skills).
[^3]: Simon Willison, ["Cognitive debt"](https://simonwillison.net/2026/Feb/15/cognitive-debt/).
[^4]: Margaret-Anne Storey, ["Cognitive Debt"](https://margaretstorey.com/blog/2026/02/09/cognitive-debt/).

## License

MIT
