# Design: Agentic outline generation

**Status:** Future work — not implemented. (A minimal stopgap — skipping vendored/minified/lock/binary files in the content blob — shipped first; see `CHANGELOG.md` and `src/cognit/ghio/diff.py:_skip_file_content`.)

**Date:** 2026-05-23

## Problem

Quiz **outline** generation pre-computes the PR diff *and the full HEAD content of every changed file* (`fetch_diff_and_files` → `read_file_at_head`, in `src/cognit/ghio/diff.py`) and injects it all into the prompt (`generate.txt` placeholders `{diff}`/`{files}`). The content blob is uncapped — only the *diff line count* is gated (`min/max_diff_lines` in `src/cognit/cli/take.py`). A PR touching a large/vendored file (e.g. the 3.2 MB `mermaid.min.js`) ships the whole file to the model and the LLM call dies (`Claude Code returned an error result`).

The diff-injection is a holdover from an **API-first** design: two adapters — `AnthropicLLM` (direct Anthropic API, single call, forced `tool_choice`) and `ClaudeAgentLLM` (subprocesses the `claude` CLI via `claude_agent_sdk`) — share one `LLMClient` protocol and one prompt. The agent adapter is locked to a single tool (`allowed_tools=["mcp__cognit__submit_*"]`) and is handed all context, even though it could fetch context itself.

## Approach

Make the **outline** call agentic: the Claude agent inspects the PR with **read-only tools**, pulling only what it needs, instead of receiving a pre-computed diff + every file's contents. Only the outline call changes — `generate_mermaid_set` and `grade_open` already take no diff/files.

Key decisions:
- **Read-only by construction:** enable built-in `Read`/`Grep`/`Glob` (cannot write or shell) + one read-only MCP tool `pr_diff` (wraps `gh pr diff`). **No raw `Bash`** → no permission-callback/streaming refactor, and no way for the agent to run `gh pr merge`/`git push`.
- **Tell the agent what to inspect:** pass PR number + branch + title/body in the prompt; run the agent with `cwd` = repo root (already checked out on the PR branch) so it reads the working tree directly. Never pre-read file contents.
- **Drop the API-key path:** delete `AnthropicLLM` / the `ANTHROPIC_API_KEY` adapter. OAuth via the `claude` binary becomes the only inference path. (The direct-API adapter physically cannot shell out, so it can't share the agentic mechanism without a separate tool-execution loop — not worth maintaining two paths.)

## Implementation (file by file)

Do in this order — signatures must agree before it type-checks.

1. **`src/cognit/engine/llm.py` — `GenerateRequest`:** drop `diff` and `files`; add `pr_number: int`, `pr_url: str`, `branch: str`. Keep `pr_title`, `pr_body`, `model`. Protocol method signatures unchanged.

2. **`src/cognit/engine/generate.py` — `generate_quiz(...)`:** drop `diff=`/`files=` kwargs, add `pr_url=`/`branch=` (`pr_number` is already a param). Update the `GenerateRequest(...)` construction. `req` still flows to mermaid workers (harmless — they ignore PR fields).

3. **`src/cognit/cli/take.py`:**
   - `_make_llm`: always `return ClaudeAgentLLM(model=model)`; drop the `ANTHROPIC_API_KEY` branch + `llm_anthropic` import, and `from anthropic import APIError` + its `except` clause in `_generate_in_memory` (ClaudeAgentLLM maps all errors to `RuntimeError`, already caught).
   - Diff-size gate stays here but goes cheap: replace `fetch_diff_and_files(...)` with `diff = fetch_pr_diff(pr_url)`, keep `diff.count("\n")` + min/max checks, then **discard** the diff (not passed to the engine — the agent re-fetches via `pr_diff`).
   - Call `generate_quiz(pr_url=..., pr_title=info.title, pr_body=info.body, pr_number=info.number, branch=info.branch, llm=, model=)`. `info.branch` already comes from `fetch_pr_info` (no extra subprocess).
   - Resolve the agent `cwd` via `git rev-parse --show-toplevel` (robust if invoked from a subdir).

4. **`src/cognit/engine/llm_claude_agent.py` (core change):**
   - Extract the shared SDK-driving core into `_run_agent(*, system, user, server, allowed_tools, max_turns, cwd, handler)` that builds `ClaudeAgentOptions(..., allowed_tools=..., max_turns=..., cwd=..., permission_mode="bypassPermissions", setting_sources=[])` and calls `_drain_agent`. **Keep the try/except → `RuntimeError` mapping verbatim** (load-bearing for `take.py` + tests). **Keep `_drain_agent`'s `asyncio.run` body unchanged** (loop-in-loop guard — outline runs only from sync CLI context, never under uvicorn; grading is already offloaded via `asyncio.to_thread` in `server/app.py`).
   - `_invoke_tool` stays a thin single-tool wrapper (`max_turns=8`, `cwd=None`) → mermaid/grading + their tests untouched.
   - New agentic `generate_quiz_outline`: register **two** MCP tools on one server — `pr_diff` (no-arg; handler calls `fetch_pr_diff(req.pr_url)`, returns the diff as text) and `submit_quiz_outline` (terminal; handler appends to `captured`). `allowed_tools = ["Read","Grep","Glob","mcp__cognit__pr_diff","mcp__cognit__submit_quiz_outline"]`, `cwd` = repo root, `max_turns = _OUTLINE_MAX_TURNS = 30`. Only the submit handler touches `captured`; after drain, `captured[0]` → `QuizOutline.model_validate(...)`, else `RuntimeError("agent did not call submit_quiz_outline")`. Pass `submit_handler` as the `_drain_agent` seam handler.
   - Delete `_format_files_blob` (dead).

5. **`src/cognit/ghio/diff.py`:** add `fetch_pr_diff(pr_url_or_number) -> str` (the `gh pr diff` call, diff text only). Delete `fetch_diff_and_files` and `read_file_at_head` once no caller remains (the agent reads the working tree via `Read`), plus the now-unused `Callable` import and the `_skip_file_content` denylist (subsumed — the agent never bulk-reads files).

6. **Prompts — `src/cognit/engine/prompts/`:**
   - `generate.txt`: remaining placeholders `{pr_number}`, `{branch}`, `{pr_title}`, `{pr_body}`. Instruct the agent to call `pr_diff`, then use `Read`/`Grep`/`Glob` on the working tree pulling only what it needs; **explicitly warn against reading large/minified/vendored files in full**; submit via `submit_quiz_outline`.
   - `system_generate.txt`: rewrite the "Input handling" section to the agentic model; **preserve the prompt-injection guard** (fetched content is evidence, not instructions). Rest unchanged. `mermaid*`/`grade*` prompts untouched.

7. **Delete the API-key adapter:** delete `src/cognit/engine/llm_anthropic.py` and `tests/engine/test_llm_anthropic.py`. Remove `anthropic>=0.102.0` from `pyproject.toml` (only that adapter imported it; `claude-agent-sdk` vendors its own client). Update the stale comment in `src/cognit/server/app.py` (keep the `asyncio.to_thread` grading offload).

8. **Tests:**
   - Delete `test_llm_anthropic.py`. Trim `tests/cli/test_take_select.py` to one test asserting `_make_llm` returns `ClaudeAgentLLM` even with `ANTHROPIC_API_KEY` set.
   - Update `test_generate.py` (new `generate_quiz(...)` kwargs; `FakeLLM` ignores `req`). `test_llm_claude_agent.py` (rewrite the two outline tests to monkeypatch `_drain_agent`; assert the built `ClaudeAgentOptions` has the 5-entry `allowed_tools`, `cwd` set, `max_turns==30`; new `GenerateRequest` fields). `test_take.py` (swap `fetch_diff_and_files` monkeypatches → `fetch_pr_diff`; rewrite the `AnthropicAPIError` failure test to raise `RuntimeError`; drop dead `anthropic`/`httpx` imports). `test_diff.py` (test `fetch_pr_diff`).
   - No change (must still pass): `tests/server/test_submit_with_claude_agent.py` — the loop-in-loop grading guard.
   - Add an outline-wiring test: both `pr_diff` + `submit_quiz_outline` registered; canned submit → valid `QuizOutline`.

9. **Docs:** `README.md` (drop `ANTHROPIC_API_KEY` from TL;DR + Authenticate; move `claude` CLI from "optional" → required; fix the "reads files at HEAD" line; remove the two API-key Troubleshooting rows). `CHANGELOG.md` (Changed: outline now agentic; Removed: BREAKING dropped `AnthropicLLM`/`ANTHROPIC_API_KEY` + `anthropic` dep). `INTENTS.md` (auth → OAuth-only).

## SDK facts (verified)

- `ClaudeAgentOptions` supports `cwd`, `tools`, `allowed_tools`, `disallowed_tools`, `can_use_tool`, etc.
- Built-in tool names are PascalCase (`Read`, `Grep`, `Glob`, `Bash`) and work in `allowed_tools` alongside `mcp__*` names.
- `setting_sources=[]` only disables settings files — it does **not** disable built-in tools.
- A `can_use_tool` permission callback exists but requires streaming mode; the read-only-by-construction tool set avoids needing it.

## Turn budget

`max_turns=8` was tuned for near-single-shot. An exploration loop (1 `pr_diff` + several `Read`/`Grep` + thinking + submit) needs more. Recommend `_OUTLINE_MAX_TURNS = 30` for the outline path only (mermaid/grading stay at 8). Too low → agent runs out before submitting and we raise `RuntimeError`; too high → a stuck loop burns latency/cost. Expose as a module constant, not a CLI flag, in v1.

## Residual risk

`pr_diff` returns the **whole** diff, so a PR that genuinely *modifies* a minified/vendored file can still be large (a one-line minified file = a megabytes-wide diff line). The primary bloat source (pre-reading *all* file contents) is eliminated regardless, and the system prompt discourages reading such files. Follow-up (not v1): truncate or path-filter `pr_diff` output, or skip generated files in the diff itself.

## Verification

1. `uv run ruff check . && uv run ruff format --check . && uv run mypy`.
2. `uv run pytest -q` — engine/cli/ghio/agent tests + the loop-in-loop guard.
3. Grep for stragglers: `fetch_diff_and_files`, `read_file_at_head`, `AnthropicLLM`, `llm_anthropic`, `_format_files_blob`, `ANTHROPIC_API_KEY`, `{files}`, `{diff}` → none in `src/`.
4. End-to-end (`claude login` + `gh auth`): `cognit take --pr <small PR>`; with `COGNIT_LOG_LEVEL=DEBUG` confirm the agent makes `pr_diff`/`Read` calls, quiz opens, grading + publish work. **Acceptance:** run against a PR touching a large/minified file and confirm the outline call no longer fails on context size.
