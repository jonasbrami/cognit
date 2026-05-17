# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-17

### Added

- **Generator GitHub Action** (`actions/quizz-generate`): triggers on `pull_request` events, calls GitHub Models, posts a quiz comment to the PR.
- **`quizz take` CLI**: auto-detects the PR for the current branch, opens a local browser quiz with rendered mermaid diagrams via `mermaid.js`, posts an answers comment back to the PR, and polls for the grader's results.
- **Grader GitHub Action** (`actions/quizz-grade`): triggers on `issue_comment` events, LLM-grades the open question, posts a results comment.
- **Four question types**: multiple choice, mermaid-diagram selection, open (LLM-graded), true/false.
- **Engine module** (`quizz.engine`): GitHub-agnostic schema + generation + grading logic, reusable by a future v2 GitHub App.
- **Comment serialization** (`quizz.comment`): lossless markdown ↔ Pydantic roundtrip with embedded JSON state.
- **CLI commands**: `quizz take`, `quizz generate` (internal — used by the Action), `quizz grade` (internal — used by the Action).
- Mermaid syntax validation via `mmdc --parse` with up-to-2 retries and graceful skip when `mmdc` is unavailable.
- Configuration via Action inputs: `model`, `min-diff-lines`, `max-diff-lines`, `version`.
- PR-level escape hatch: `quiz: skip` in PR body suppresses generation.
- Example workflows in `.github/examples/` for users to copy.

### Known limitations

- Fork PRs not supported in v1 (the generator's `GITHUB_TOKEN` is read-only on forks).
- Single LLM provider (GitHub Models) — fleet-of-LLMs deferred to v2.
- No team-specific knowledge injection (Skills) — deferred to v2.
- No GitHub App / Marketplace App — deferred to v2.
