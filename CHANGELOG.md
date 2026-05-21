# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **BREAKING — CLI collapsed to a single `quizz take` command.** `take` now auto-generates the quiz comment on the PR if none exists yet (calls the LLM with the diff, posts the rendered markdown), then opens the browser and grades in-session as before. The author runs one command instead of three. New flags on `take`: `--min-diff-lines` (default 50) and `--max-diff-lines` (default 2000) inherit from the old `quizz generate`. The engine layer (`engine/generate.py`, `engine/grade.py`) is unchanged — a future webhook or GitHub App can still call it directly.
- **UI redesign**: `quizz take` now uses a github-native design — replaces the editorial (paper/serif/narrow) UI. Spec in `UI-REDESIGN.md`.

### Added
- `/publish` endpoint now returns `comment_url` so the UI can deep-link to the posted comment.
- Playwright integration tests in `tests/server/test_ui_flow.py` driving the question → results → published flow.

### Fixed
- **OAuth token rotation no longer 401s mid-session.** `AnthropicLLM` previously cached the Claude Code OAuth token at `__init__` and never re-read `~/.claude/.credentials.json`. A long-running `quizz take` (auto-generate → user answers → submit) would 401 on the grading call when `claude` rotated the token in between. The client now retries once on `AuthenticationError` with a freshly-read token, recovering automatically. API-key auth is unaffected — a 401 there is a real configuration problem and still bubbles up immediately.
- XSS hardening: quiz JSON injected into inline `<script>` is `</`-escaped; PR URL substituted into `href=` attributes and the JS global is properly HTML/JSON escaped.
- Mermaid `securityLevel` changed from `"loose"` to `"strict"` — prevents HTML rendering inside LLM-generated node labels.
- Inline backtick-code rendering in prompts (regression from the rewrite).
- Submit button is disabled until all questions are answered.
- Keyboard navigation + ARIA on MCQ / TF / Diagram options (a11y regression from the rewrite).

### Removed
- **BREAKING — `quizz generate` and `quizz grade` CLI commands.** Their behaviour is absorbed into `quizz take`: generation runs automatically when no quiz comment exists on the PR; grading runs in-session via the local FastAPI server. Tests for these CLI surfaces were deleted; engine-level tests for generation and grading remain intact.
- **GitHub Actions wrappers** (`actions/quizz-generate`, `actions/quizz-grade`) and the matching `.github/examples/` workflows. They were prototyped end-to-end but hit two compounding issues with GitHub Models (strict schema rejection + malformed free-form output from `gpt-4o-mini`). v1 ships as **local CLI only**; the auto-trigger Action ambition is now **dropped, not deferred** — the CLI no longer exposes a separate `generate` entrypoint to wrap.

## [0.1.0] — 2026-05-17

### Added

- **`quizz generate` CLI**: reads diff via `gh pr diff`, calls an LLM (Anthropic via tool use by default; GitHub Models as alternate), validates mermaid diagrams via `mmdc --parse`, posts a quiz comment to the PR.
- **`quizz take` CLI**: auto-detects the PR for the current branch, opens a local FastAPI server with a polished browser UI (mermaid diagrams rendered client-side via `mermaid.js` UMD bundle), posts an answers comment back to the PR, and polls for the results comment.
- **`quizz grade` CLI**: locates quiz + answers comments, LLM-grades the open question, posts a results comment with per-question feedback.
- **Four question types**: multiple choice, mermaid-diagram selection (auto-relabeled to neutral A/B/C/D to prevent answer leak), open (LLM-graded), true/false.
- **Anthropic LLM adapter** via tool use (guaranteed-schema output). Auth resolution: explicit `api_key` → `ANTHROPIC_API_KEY` env var → Claude Code OAuth at `~/.claude/.credentials.json`.
- **GitHub Models LLM adapter** (OpenAI-compatible) as an alternate provider.
- **Engine module** (`quizz.engine`): GitHub-agnostic schema + generation + grading logic.
- **Comment serialization** (`quizz.comment`): lossless markdown ↔ Pydantic roundtrip with embedded JSON state.
- Mermaid validation pipeline: `mmdc --parse` with up-to-2 retries; drop mermaid Q + add replacement MCQ on terminal failure.
- PR-level escape hatch: `quiz: skip` in PR body suppresses generation.

### Known limitations

- Fork PRs not supported in v1 (`gh pr comment` requires write access).
- Single LLM provider per invocation — fleet-of-LLMs deferred to v2.
- No team-specific knowledge injection (Skills) — deferred to v2.
- No GitHub App / Marketplace App — deferred to v2.
- No CI auto-trigger — local CLI only. See "Removed" above.
