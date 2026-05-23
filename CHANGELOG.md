# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Outline generation is now agentic.** A read-only Claude agent (built-in Read/Grep/Glob restricted via the SDK `tools` parameter, plus an in-process `pr_diff` MCP tool) inspects the PR and pulls only the context it needs — replacing the previous approach of pre-reading every changed file's contents and injecting them wholesale into the prompt.
- **Renamed `quizz` → `cognit`.** Package, CLI command, env var (`QUIZZ_LOG_LEVEL` → `COGNIT_LOG_LEVEL`), PR comment markers, cache dir (`$TMPDIR/quizz/` → `$TMPDIR/cognit/`), and MCP server name all move to `cognit`. The generic noun "quiz" (question artifact, `Quiz` model, `quiz.js`) is unchanged. Pre-1.0, nothing was published under the old name.
- **In-memory-only quiz storage.** `cognit take` no longer posts the quiz as a PR comment. The quiz is generated in memory and cached locally at `$TMPDIR/cognit/<sha1(pr_url)[:16]>.json`. Re-running `cognit take` against the same PR (e.g. after closing the browser) reuses the cached quiz instead of regenerating, so a closed-tab recovery doesn't pay another LLM bill. The PR thread now carries **at most one comment per take session**, only if the author clicks Publish — and that comment is self-contained (question prompts + author answers inlined via `render_results_inlined`). Reviewers no longer see an answer key in plaintext on the PR.
  - `/publish` now requires a prior `/submit` (returns 400 otherwise) because the inlined comment needs the cached answers.
  - Older PRs may still have legacy `<!-- cognit:quiz v1 -->` comments; they're dormant and harmless. New runs ignore them.
- **BREAKING — CLI collapsed to a single `cognit take` command.** `take` now auto-generates the quiz comment on the PR if none exists yet (calls the LLM with the diff, posts the rendered markdown), then opens the browser and grades in-session as before. The author runs one command instead of three. New flags on `take`: `--min-diff-lines` (default 50) and `--max-diff-lines` (default 2000) inherit from the old `cognit generate`. The engine layer (`engine/generate.py`, `engine/grade.py`) is unchanged — a future webhook or GitHub App can still call it directly.
- **UI redesign**: `cognit take` now uses a github-native design — replaces the editorial (paper/serif/narrow) UI. Spec in `UI-REDESIGN.md`.

### Added
- `/publish` endpoint now returns `comment_url` so the UI can deep-link to the posted comment.
- Playwright integration tests in `tests/server/test_ui_flow.py` driving the question → results → published flow.

### Fixed
- **Quiz generation no longer ships giant vendored files into the prompt.** `fetch_diff_and_files` now skips a denylist of vendored/minified/lockfile/binary paths (`*.min.js`, `*.min.css`, `*.lock`, `package-lock.json`, `pnpm-lock.yaml`, images, fonts, PDFs) when inlining changed-file contents. The diff still lists them as touched, but their full text is omitted — fixing context-window blowups on PRs that touch large files like the 3.2 MB vendored `mermaid.min.js`. The agentic outline rework supersedes file-content injection entirely; the same denylist now filters the diff the agent fetches via `pr_diff`.
- **Mermaid validator no longer silently skips when `mmdc` is missing.** Validation now layers: native `mmdc` if on PATH → dockerised parse-only image (lazily built from `src/cognit/engine/_mermaid_docker/` on first use, ~200 MB without Chromium) → Python regex backstop. The backstop catches the common LLM failure modes (missing diagram header, grossly unbalanced brackets, **`[/text]` parallelogram-shape traps** when labels contain URL-like paths — the bug that surfaced in the PR #4 smoke against this very repo). The artisan system prompt also explicitly forbids leading `/` or `\` in node labels now, so generation retries clean output instead of relying on validation to catch it.
- **Debug logging via `COGNIT_LOG_LEVEL`.** Surfaces which validator layer is being used per diagram, cache hits/misses, docker image build state, etc. Default level is WARNING (quiet); set `COGNIT_LOG_LEVEL=DEBUG` to trace internal decisions.
- XSS hardening: quiz JSON injected into inline `<script>` is `</`-escaped; PR URL substituted into `href=` attributes and the JS global is properly HTML/JSON escaped.
- Mermaid `securityLevel` changed from `"loose"` to `"strict"` — prevents HTML rendering inside LLM-generated node labels.
- Inline backtick-code rendering in prompts (regression from the rewrite).
- Submit button is disabled until all questions are answered.
- Keyboard navigation + ARIA on MCQ / TF / Diagram options (a11y regression from the rewrite).

### Removed
- **BREAKING — Direct Anthropic API-key path removed.** `AnthropicLLM` and the `ANTHROPIC_API_KEY` env var are gone, and the `anthropic` Python package is no longer a dependency. OAuth via the `claude` CLI is now the only inference path; `claude login` is required.
- **BREAKING — `cognit generate` and `cognit grade` CLI commands.** Their behaviour is absorbed into `cognit take`: generation runs automatically when no quiz comment exists on the PR; grading runs in-session via the local FastAPI server. Tests for these CLI surfaces were deleted; engine-level tests for generation and grading remain intact.
- **GitHub Actions wrappers** (`actions/cognit-generate`, `actions/cognit-grade`) and the matching `.github/examples/` workflows. They were prototyped end-to-end but hit two compounding issues with GitHub Models (strict schema rejection + malformed free-form output from `gpt-4o-mini`). v1 ships as **local CLI only**; the auto-trigger Action ambition is now **dropped, not deferred** — the CLI no longer exposes a separate `generate` entrypoint to wrap.

## [0.1.0] — 2026-05-17

### Added

- **`cognit generate` CLI**: reads diff via `gh pr diff`, calls an LLM (Anthropic via tool use by default; GitHub Models as alternate), validates mermaid diagrams via `mmdc --parse`, posts a quiz comment to the PR.
- **`cognit take` CLI**: auto-detects the PR for the current branch, opens a local FastAPI server with a polished browser UI (mermaid diagrams rendered client-side via `mermaid.js` UMD bundle), posts an answers comment back to the PR, and polls for the results comment.
- **`cognit grade` CLI**: locates quiz + answers comments, LLM-grades the open question, posts a results comment with per-question feedback.
- **Four question types**: multiple choice, mermaid-diagram selection (auto-relabeled to neutral A/B/C/D to prevent answer leak), open (LLM-graded), true/false.
- **Anthropic LLM adapter** via tool use (guaranteed-schema output). Auth resolution: explicit `api_key` → `ANTHROPIC_API_KEY` env var → Claude Code OAuth at `~/.claude/.credentials.json`.
- **GitHub Models LLM adapter** (OpenAI-compatible) as an alternate provider.
- **Engine module** (`cognit.engine`): GitHub-agnostic schema + generation + grading logic.
- **Comment serialization** (`cognit.comment`): lossless markdown ↔ Pydantic roundtrip with embedded JSON state.
- Mermaid validation pipeline: `mmdc --parse` with up-to-2 retries; drop mermaid Q + add replacement MCQ on terminal failure.
- PR-level escape hatch: `quiz: skip` in PR body suppresses generation.

### Known limitations

- Fork PRs not supported in v1 (`gh pr comment` requires write access).
- Single LLM provider per invocation — fleet-of-LLMs deferred to v2.
- No team-specific knowledge injection (Skills) — deferred to v2.
- No GitHub App / Marketplace App — deferred to v2.
- No CI auto-trigger — local CLI only. See "Removed" above.
