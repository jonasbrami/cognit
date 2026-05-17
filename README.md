# quizz

> Voluntary, opt-in PR-author comprehension quizzes. Surface the gap between what you think your code does and what it actually does — before you merge.

## What this is

A GitHub-friendly tool that quizzes the **author** of a pull request (not the reviewer) on the code they're about to merge. Three pieces:

- A **generator GitHub Action** that posts a quiz comment when you open a PR.
- A **CLI** (`quizz take`) that opens the quiz in your local browser.
- A **grader GitHub Action** that scores your submitted answers and posts results.

Like CI checks, linters, or pre-commit hooks: opt-in. Failing doesn't block merge — the value is the "aha" moment when you realize the code does something you didn't expect.

## How it works

1. You open a PR. The **generator Action** reads the diff, calls an LLM (GitHub Models, free tier), and posts a quiz comment with 5 questions: multiple choice, mermaid-diagram selection, true/false, and an open question.
2. You run `quizz take` locally. It reads the quiz comment, opens a polished browser quiz on `localhost`, you answer.
3. You submit. The CLI grades the deterministic questions immediately, posts an answers comment to the PR.
4. The **grader Action** fires, LLM-grades the open question, and posts a results comment with per-question feedback.
5. Your browser polls and shows you the final score plus explanations for anything you got wrong.

## Quickstart

### 1. Install the CLI

```bash
pipx install quizz
# or
uv tool install quizz
```

### 2. Add the two workflows to your repo

Copy [`.github/examples/quizz-generate.yml`](.github/examples/quizz-generate.yml) and [`.github/examples/quizz-grade.yml`](.github/examples/quizz-grade.yml) into your repo's `.github/workflows/`. Update the `uses:` reference to point at this repo's tagged release.

### 3. Open a PR

The generator runs, posts a quiz comment within ~60 seconds. Run `quizz take` to take it. Submit, watch results appear.

## Configuration

Both Actions accept inputs:

| Input | Default | Description |
|---|---|---|
| `version` | `0.1.0` | Pinned `quizz` PyPI version |
| `model` | `gpt-4o-mini` | LLM model (GitHub Models) |
| `min-diff-lines` | `50` | Skip PRs below this many changed lines (generator only) |
| `max-diff-lines` | `2000` | Skip PRs above this many changed lines (generator only) |

To suppress quiz generation on a specific PR, include `quiz: skip` in the PR description.

## Rate limits

GitHub Models free tier: ~50 high-tier requests/day and ~150 mini/day per account. For higher volume, switch to paid GitHub Models or BYO an OpenAI-compatible endpoint.

## Status

v1.0 ships:
- Generator + grader Actions
- `quizz take` CLI with browser UI
- 4 question types (MCQ, mermaid, open, true/false)
- GitHub Models integration
- Single LLM provider, single repo, in-PR-comments state

Future (see [`INTENTS.md`](INTENTS.md)):
- GitHub App (no per-repo workflow file)
- Fleet of LLMs for question diversity
- Skills integration (team domain knowledge in generation prompts)
- IDE integration

## Philosophy

> The risk isn't bad code per se; it's **false confidence in code that looks reasonable but does something subtly different from what the developer expects**.

The quiz is the diagnostic; the explanation of the right answer is the medicine. The north star is to maximize the utility of human attention — let LLMs do the heavy lifting of probing understanding so the limited human time spent on a PR is spent on what genuinely needs a human mind.

## License

MIT
