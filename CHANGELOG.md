# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] — 2026-05-29

Learning-acceleration UX — the quiz now teaches while you take it, with the code in front of you.

### Added
- **Inline code context per question.** A question can carry an `anchor` (`{path, start_line, end_line}`) and the browser shows that exact diff hunk inline, in a collapsible panel (closed by default) clipped to the anchored lines ± a few lines of context, with the anchored lines highlighted. The filename links to the file on the PR's branch in GitHub, deep-linked to the line range — and only when it's actually one of the PR's changed files, so the link never 404s. The generation prompt now emits anchors. (#26, #29, #30)
- **Diff coverage map.** A sidebar panel lists the PR's changed files with covered / uncovered markers and an "N of M files probed" count, so you can see at a glance what the quiz does and doesn't probe. Backed by a new `GET /changed-files`; coverage uses the same exact / repo-relative-suffix / basename matching as `file_diff`. (#27)
- **Practice mode with immediate per-question feedback.** Deterministic questions (mcq / tf / mermaid) now reveal — correct/incorrect, the right answer, and the explanation — the moment you commit, instead of only after Submit. A sidebar "Reveal answers as I go" toggle switches between this (default) and the classic batch/exam flow, sticky per browser. Open questions still grade at Submit. (#28)
- **Confidence rating + calibration.** Before a practice-mode reveal you rate how sure you are (1–5); the revealed card flags miscalibration — "confident but wrong" or "right but you weren't sure". The rating is persisted server-side (`POST /confidence`, in the `QuizState` snapshot) and surfaced to the host agent via `get_answers` and the grade result (`QuestionResult.confidence`), so it can re-probe confident-but-wrong answers or drill concepts you were unsure about. (#31)

### Notes
- All additive and backward-compatible: quizzes and cached snapshots without the new optional fields (`anchor`, `confidences`) load unchanged, and exam mode reproduces the prior answering flow byte-for-byte.

## [0.3.0] — 2026-05-28

### Changed
- **`cognit take` now runs the quiz interactively on Claude Code itself.** Instead of a one-shot generation call, the CLI `execvpe`s into a *confined* interactive `claude` session wired to an in-process MCP server (the quiz "render API"). You converse in the terminal to steer the quiz — *"skip Q2, make it harder"*, *"focus on the migration"*, *"grade me"* — while the browser polls the shared `QuizState` and re-renders only the questions that change (so steering one question never clobbers answers you've already typed). The two surfaces never talk to each other directly; they only read and write `QuizState`, which writes through to a JSON snapshot so a refresh or crash loses nothing.
- **The browser is now a thin projection of `QuizState`.** `GET /state` is polled every 1s; the page renders one of `waiting / answering / results / published`. Browser Submit and the agent's `grade` MCP tool converge on the same `grade_state` handler, so grading is identical whether you click Submit in the page or say "grade me" in the terminal.
- **Inference split into two distinct mechanisms.** The host session *is* the interactive `claude` CLI binary (so Claude Code OAuth/Max subscribers reach Sonnet and Opus); the only other call — the open-question grader — uses the Claude Agent SDK, which itself drives the same binary.

### Added
- **Read-confinement `PreToolUse` hook (`cognit.mcp.confine`).** Resolves every `Read`/`Grep`/`Glob` path against the repo root and denies traversals (`../`, absolute paths) — fail-closed. A prompt-injected PR body cannot coax the session into reading `~/.ssh/id_rsa`.
- **Coarse tool gate via the host CLI.** The session is launched with `--tools "Read Grep Glob" --strict-mcp-config --setting-sources user --permission-mode bypassPermissions`. `bypassPermissions` only suppresses prompts; the real safety boundary is `--tools`, which controls which tools *exist* — no `Bash`, no `Write`, no `Edit`. The `file_diff` MCP tool exposes one fixed `subprocess.run` argv instead of a restricted git, since `git` is an RCE surface via config, external-diff drivers, and aliases.
- **Handler-owned grading and human-gated publish.** MCQ / mermaid / true-false scored deterministically; open answers go to the strict SDK grader. Posting results to the PR is a browser button only, never an agent tool — so the model cannot publish on its own even if it's hijacked.
- **Debug logging.** `COGNIT_LOG_LEVEL=DEBUG` now also captures the host session's full transcript via `claude --debug-file <path>` to `$TMPDIR/cognit/<digest>-claude-debug.log`.
- **README rewrite.** New mermaid diagrams (interaction loop, end-to-end sequence, render-state machine), HD screenshots (DPR 2), and a real screen-recorded demo gif of `cognit take` in action.

### Fixed
- **`/publish` now surfaces failures.** Wraps the `gh` call in try/except → returns 502 with the `gh` stderr inlined, instead of a bare 500. The browser alerts with the actual reason.
- **Generation prompt documents the per-type schema asymmetry** (`mcq.answer` = full option text, `mermaid.answer` = key, `tf.answer` = boolean, `open` has no answer/explanation) and tells the agent to trust `changed_files` instead of Glob-ing the tree (which would flood with `.venv`/`node_modules`).

## [0.2.1] — 2026-05-25

### Fixed
- **PyPI package classifiers / Python-version badge.** Added trove `classifiers` (supported Python versions, license, topic) to package metadata. The `pypi/pyversions` shield now resolves to `3.12`/`3.13` instead of showing "missing", and the PyPI page advertises supported versions.

## [0.2.0] — 2026-05-25

### Changed
- **Quiz generation is now a single agentic task.** A read-only Claude agent (built-in Read/Grep/Glob restricted via the SDK `tools` parameter, plus an in-process `pr_diff` MCP tool) inspects the PR and pulls only the context it needs — replacing the previous approach of pre-reading every changed file's contents and injecting them wholesale into the prompt. The agent now also renders mermaid diagrams itself and submits the complete quiz in one `draft_quiz` call, collapsing the former two-stage flow (an outline call followed by a `ThreadPoolExecutor` fan-out of per-diagram "artisan" calls). A `PreToolUse` validation hook on the submit tool checks quiz shape, mermaid syntax, and visual uniformity across the four diagrams; on failure it denies with a precise reason and the agent self-corrects within the same turn. If a diagram can't be made valid within the turn budget, generation fails — there is no longer any drop-and-replace fallback.
- **Renamed `quizz` → `cognit`.** Package, CLI command, env var (`QUIZZ_LOG_LEVEL` → `COGNIT_LOG_LEVEL`), PR comment markers, cache dir (`$TMPDIR/quizz/` → `$TMPDIR/cognit/`), and MCP server name all move to `cognit`. The generic noun "quiz" (question artifact, `Quiz` model, `quiz.js`) is unchanged. Pre-1.0, nothing was published under the old name.
- **In-memory-only quiz storage.** `cognit take` no longer posts the quiz as a PR comment. The quiz is generated in memory and cached locally at `$TMPDIR/cognit/<sha1(pr_url)[:16]>.json`. Re-running `cognit take` against the same PR (e.g. after closing the browser) reuses the cached quiz instead of regenerating, so a closed-tab recovery doesn't pay another LLM bill. The PR thread now carries **at most one comment per take session**, only if the author clicks Publish — and that comment is self-contained (question prompts + author answers inlined via `render_results_inlined`). Reviewers no longer see an answer key in plaintext on the PR.
  - `/publish` now requires a prior `/submit` (returns 400 otherwise) because the inlined comment needs the cached answers.
  - Older PRs may still have legacy `<!-- cognit:quiz v1 -->` comments; they're dormant and harmless. New runs ignore them.
- **BREAKING — CLI collapsed to a single `cognit take` command.** `take` now auto-generates the quiz comment on the PR if none exists yet (calls the LLM with the diff, posts the rendered markdown), then opens the browser and grades in-session as before. The author runs one command instead of three. The engine layer (`engine/generate.py`, `engine/grade.py`) is unchanged — a future webhook or GitHub App can still call it directly.
- **UI redesign**: `cognit take` now uses a github-native design — replaces the editorial (paper/serif/narrow) UI.

### Added
- `/publish` endpoint now returns `comment_url` so the UI can deep-link to the posted comment.
- Playwright integration tests in `tests/server/test_ui_flow.py` driving the question → results → published flow.

### Fixed
- **Quiz generation no longer ships giant vendored files into the prompt.** `fetch_diff_and_files` now skips a denylist of vendored/minified/lockfile/binary paths (`*.min.js`, `*.min.css`, `*.lock`, `package-lock.json`, `pnpm-lock.yaml`, images, fonts, PDFs) when inlining changed-file contents. The diff still lists them as touched, but their full text is omitted — fixing context-window blowups on PRs that touch large files like the 3.2 MB vendored `mermaid.min.js`. The agentic generation rework supersedes file-content injection entirely; the same denylist now filters the diff the agent fetches via `pr_diff`.
- **Mermaid validator no longer silently skips when `mmdc` is missing.** Validation now layers: native `mmdc` if on PATH → dockerised parse-only image (lazily built from `src/cognit/engine/_mermaid_docker/` on first use, ~200 MB without Chromium) → Python regex backstop. The backstop catches the common LLM failure modes (missing diagram header, grossly unbalanced brackets, **`[/text]` parallelogram-shape traps** when labels contain URL-like paths — the bug that surfaced in the PR #4 smoke against this very repo). The generation system prompt also explicitly forbids leading `/` or `\` in node labels now, so the submit-validation hook drives the agent to clean output instead of relying on validation to catch it.
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
- **`--min-diff-lines` / `--max-diff-lines` flags and the diff-size skip gate.** `cognit take` is an explicit action, so it no longer auto-skips a PR by diff size (a confusing dead-end that second-guessed a deliberate command). The `quiz: skip` PR-body opt-out remains. This also dropped a redundant `fetch_pr_diff` call in the CLI pre-flight — the generation agent re-fetches the diff itself.

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
- Mermaid validation pipeline: `mmdc --parse` gates every diagram at submit time; the generation agent self-corrects invalid diagrams in-turn rather than dropping the question or substituting a replacement MCQ.
- PR-level escape hatch: `quiz: skip` in PR body suppresses generation.

### Known limitations

- Fork PRs not supported in v1 (`gh pr comment` requires write access).
- Single LLM provider per invocation — fleet-of-LLMs deferred to v2.
- No team-specific knowledge injection (Skills) — deferred to v2.
- No GitHub App / Marketplace App — deferred to v2.
- No CI auto-trigger — local CLI only. See "Removed" above.
