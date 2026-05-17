# quizz

> Voluntary, opt-in PR-author comprehension quizzes. Surface the gap between what you think your code does and what it actually does — before you merge.

## What this is

A local CLI that quizzes the **author** of a pull request (not the reviewer) on the code they're about to merge. Three subcommands:

- `quizz generate --pr <url> --post` — calls an LLM, generates a quiz from the diff, posts it as a PR comment.
- `quizz take` — auto-detects the PR for the current branch, opens the quiz in your local browser (mermaid diagrams render via `mermaid.js`), posts answers back to the PR.
- `quizz grade --pr <url>` — LLM-grades the open question, posts a results comment with per-question feedback.

Like CI checks, linters, or pre-commit hooks: opt-in. Failing doesn't block merge — the value is the "aha" moment when you realize the code does something you didn't expect.

> v1 ships as a local CLI only. The GitHub Actions wrapper that would auto-trigger the quiz on PR open is **not part of this release** — see `INTENTS.md` for the v2 roadmap.

## How it works

1. You open a PR.
2. From a checkout of the PR branch, run `quizz generate --pr <url> --post`. The CLI reads the diff via `gh`, calls an LLM (Claude via the Anthropic SDK; Claude Code OAuth or `ANTHROPIC_API_KEY`), and posts a structured quiz comment to the PR with 5 questions: 2 MCQ, 1 mermaid-diagram-pick, 1 open, 1 true/false.
3. Run `quizz take`. It fetches the quiz comment, opens `localhost` in your browser with a polished form (mermaid diagrams rendered client-side). You answer, hit Submit.
4. The CLI grades the deterministic questions immediately, posts an answers comment to the PR.
5. Run `quizz grade --pr <url>`. The LLM grades the open question, posts a results comment with per-question feedback. Your browser polls and shows the final score.

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
quizz generate --pr "$(gh pr view --json url --jq .url)" --post
quizz take
# answer in browser, submit
quizz grade --pr "$(gh pr view --json url --jq .url)"
```

## Configuration

All three subcommands accept:

| Flag | Default | Description |
|---|---|---|
| `--llm` | `auto` | `auto` / `anthropic` / `github`. `auto` picks Anthropic if a key or Claude Code OAuth is available. |
| `--model` | `gpt-4o-mini` (or `claude-sonnet-4-6` when provider=anthropic) | LLM model |

`quizz generate` additionally accepts `--min-diff-lines` (default 50), `--max-diff-lines` (2000), and `--dry-run`.

To suppress quiz generation on a specific PR, include `quiz: skip` in the PR description.

## Rate limits

- **Claude Code OAuth path**: bound by your Claude Code subscription limits (per-model RPM/daily).
- **API key path**: standard Anthropic API limits.
- The GitHub Models adapter (`--llm github`) is included for completeness but is **not the recommended path** — see `INTENTS.md` for why.

## Status

v1.0 ships:
- Local `quizz generate`, `quizz take`, `quizz grade` CLI commands.
- 4 question types (MCQ, mermaid-pick with auto-neutralized A/B/C/D labels, open, true/false).
- Anthropic adapter via tool use (guaranteed-schema output) with Claude Code OAuth fallback.
- Local FastAPI server with embedded HTML/JS/CSS + `mermaid.js` UMD bundle.

Future (see [`INTENTS.md`](INTENTS.md)):
- GitHub Action auto-trigger on PR open (removed from v1 — local CLI is the canonical path for now).
- GitHub App (no per-repo workflow file).
- Fleet of LLMs for question diversity.
- Skills integration (team domain knowledge in generation prompts).
- IDE integration.

## Philosophy

> The risk isn't bad code per se; it's **false confidence in code that looks reasonable but does something subtly different from what the developer expects**.

The quiz is the diagnostic; the explanation of the right answer is the medicine. The north star is to maximize the utility of human attention — let LLMs do the heavy lifting of probing understanding so the limited human time spent on a PR is spent on what genuinely needs a human mind.

## License

MIT
