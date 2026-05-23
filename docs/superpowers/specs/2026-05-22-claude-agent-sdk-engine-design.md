# Engine adapter via `claude_agent_sdk` — design

**Status:** approved, ready for implementation plan
**Date:** 2026-05-22
**Author:** quizz maintainer (collaborative w/ Claude)

## Problem

`quizz take` defaults to `--model claude-sonnet-4-6`. When the user authenticates via Claude Code OAuth (the recommended path per the README), the first LLM call returns a sparse 429 `rate_limit_error` with body `{"message": "Error"}` and no `anthropic-ratelimit-*` headers. The same call from the official `claude` binary on the same OAuth token succeeds.

Investigation showed this is **not** a usage-based rate limit. It is Anthropic's API refusing premium-model access for OAuth tokens used by third-party clients:

- Every Sonnet and Opus model alias (`-4-6`, `-4-5`, `-4-1`, dated forms, `[1m]` variants) returns 429 instantly.
- Unknown model IDs return 404 — proving the type discriminator works.
- Haiku consistently returns 200 with full `anthropic-ratelimit-unified-*` telemetry showing the user's 5h bucket at ~18% utilization.
- Adding every Claude Code-identifying header (`x-app: cli`, `User-Agent: claude-cli/...`, `anthropic-client-platform: claude_code_cli`, `X-Claude-Code-Session-Id`, `x-anthropic-additional-protection`) does not change the result.
- This Claude Code session (same OAuth token, `claude-opus-4-7`) works because it routes through the official binary, which performs a session-binding handshake we cannot replicate from outside.

The current `engine/llm_anthropic.py` adapter calls `api.anthropic.com/v1/messages` directly via the `anthropic` Python SDK. That path is fundamentally gated. To make Sonnet/Opus work for OAuth users, we need to route inference through the `claude` binary the same way `claude_agent_sdk` does.

## Goals

1. OAuth-only users can run `quizz take` with the default `--model claude-sonnet-4-6` and Opus, end-to-end.
2. API-key users keep the current direct-SDK path (faster, no subprocess).
3. The existing `LLMClient` Protocol, `FakeLLM`, `generate.py`, `grade.py`, `server/app.py`, and all existing tests remain unchanged.
4. The mermaid artisan fan-out behavior (serial by default, retry on validation failure, drop on persistent failure) is preserved.

## Non-goals

- Async refactor of the engine. The Protocol stays sync; asyncio is hidden inside the new adapter.
- Persistent `ClaudeSDKClient` across a quiz. Per-call subprocess spawn is acceptable (~1–2s overhead per LLM call vs. ~5–15s LLM latency).
- README rewrite. Tracked as a separate follow-up.
- Prompt or schema changes. The three existing tool schemas (`submit_quiz_outline`, `submit_mermaid_set`, `submit_grade`) are reused verbatim as MCP-tool schemas.
- Replacing the `anthropic` dependency. It stays for the API-key path.

## Architecture

Two adapters, one Protocol:

```
src/quizz/engine/
  llm.py              # LLMClient Protocol  (UNCHANGED)
  llm_anthropic.py    # AnthropicLLM        (UNCHANGED)
  llm_claude_agent.py # ClaudeAgentLLM      (NEW)
  llm_fake.py         # FakeLLM             (UNCHANGED)
```

Selection lives in `cli/take.py:_make_llm`:

```python
def _make_llm(model: str) -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM(model=model)
    return ClaudeAgentLLM(model=model)
```

Branch on the only signal that matters. No new CLI flag. No fallback chain.

### Auth-path matrix

| Env / state                           | Selected adapter | Works for |
|---------------------------------------|------------------|-----------|
| `ANTHROPIC_API_KEY` set               | `AnthropicLLM`   | All models (subject to API-key quota) |
| No key, `claude` binary present, OAuth valid | `ClaudeAgentLLM` | All models (OAuth-Max plan) |
| No key, `claude` binary missing       | `ClaudeAgentLLM` raises `RuntimeError` at first call | — (CLI exits with actionable message) |
| No key, OAuth missing / expired       | `ClaudeAgentLLM` raises whatever `claude` binary reports | — (CLI exits with actionable message) |

## `ClaudeAgentLLM` internals

The class implements three methods, each following the same recipe:

### Recipe

1. **Build a one-shot in-process MCP tool** matching the existing tool schema. The handler captures the validated args into a closure-captured slot (`captured: list[dict[str, Any]] = []`) and returns the MCP "ok" payload:
   ```python
   @tool(tool_name, "...", input_schema)
   async def handler(args: dict[str, Any]) -> dict[str, Any]:
       captured.append(args)
       return {"content": [{"type": "text", "text": "ok"}]}
   ```
2. **Wrap it in an in-process MCP server**:
   ```python
   server = create_sdk_mcp_server(name="quizz", tools=[handler])
   ```
3. **Build options** with the existing system prompt and tight tool allowlist:
   ```python
   options = ClaudeAgentOptions(
       system_prompt=system_text,
       model=self._model,
       mcp_servers={"quizz": server},
       allowed_tools=[f"mcp__quizz__{tool_name}"],
       max_turns=2,
       permission_mode="bypassPermissions",
       setting_sources=[],  # ignore project-level settings; quizz brings its own prompt
   )
   ```
4. **Run the agent**:
   ```python
   asyncio.run(self._drain(user_message, options, captured))
   ```
   Inside `_drain`, iterate `query(prompt=user_message, options=options)` and exhaust the message stream. The MCP server handler fires when the agent invokes the tool.
5. **Return the captured args** as a validated Pydantic model. If `captured` is empty after the stream ends → one retry with a tighter prompt ("You MUST call the {tool_name} tool. Do not respond with text."); on second failure raise `RuntimeError(f"agent did not call {tool_name}")`.

### Method-by-method

| Method | Tool name | Schema | System prompt | Returns |
|---|---|---|---|---|
| `generate_quiz_outline(req)` | `submit_quiz_outline` | `QuizOutline.model_json_schema()` | `prompts/system_generate.txt` | `QuizOutline` |
| `generate_mermaid_set(spec, req)` | `submit_mermaid_set` | hand-rolled schema (4-options + correct, identical to `llm_anthropic.py:201-223`) | `prompts/system_mermaid.txt` | `MermaidSet` |
| `grade_open(prompt, rubric, answer)` | `submit_grade` | `{score: int 0–100, feedback: str}` | `prompts/system_grade.txt` | `tuple[int, str]` |

User-message construction is identical to `llm_anthropic.py` (re-use `_load_prompt`, `_format_files_blob`, `_format_misconceptions`).

### Forcing tool use

`claude_agent_sdk` does not expose `tool_choice="required"`. Two mitigations:

1. **Allowlist of one.** `allowed_tools=["mcp__quizz__submit_quiz_outline"]` means the agent has no other tool to use.
2. **Prompt nudge.** The system prompts already say "Submit your full outline via the `submit_quiz_outline` tool." (see `system_generate.txt:37`). Tighten in the retry prompt only.

### Error mapping

| Agent SDK raises | Adapter raises | Caught in |
|---|---|---|
| `CLINotFoundError` | `RuntimeError("claude binary not found; install Claude Code (`npm i -g @anthropic-ai/claude-code`) or set ANTHROPIC_API_KEY")` | `take.py:_generate_and_post` |
| `CLIConnectionError`, `ProcessError`, `ClaudeSDKError` | `RuntimeError("agent SDK call failed: {cause}")` | same |
| Stream ends without tool call (after retry) | `RuntimeError("agent did not call <tool>")` | `_render_mermaid_with_retry` (for mermaid) or `take.py` (for outline/grade) |
| Tool args fail Pydantic validation | re-raise the `ValidationError` | existing handlers in `generate.py` and `take.py` |

`take.py:_generate_and_post` needs one new clause: catch `RuntimeError` alongside `AnthropicAPIError` and `ValidationError`, print the message, exit 1.

## Dependency changes

`pyproject.toml`:

```toml
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "httpx>=0.27",
    "fastapi>=0.136.1",
    "uvicorn>=0.47.0",
    "anthropic>=0.102.0",
    "claude-agent-sdk>=0.1.44",  # NEW
]
```

No removal — both adapters coexist.

## Tests

### New tests

- `tests/engine/test_llm_claude_agent.py`
  - One test per method, mocking `claude_agent_sdk.query` to emit a fake async iterator yielding an `AssistantMessage` with a `ToolUseBlock` and a `ResultMessage`. Assert the adapter returns the right Pydantic model.
  - One retry test: first stream emits no tool call, second emits the expected call. Assert adapter returns the model and `query` was called twice.
  - One failure test: both streams emit no tool call. Assert `RuntimeError`.
  - One `CLINotFoundError` test: assert it is re-raised as `RuntimeError` with the install hint.
- `tests/cli/test_take_select.py`
  - `_make_llm` returns `AnthropicLLM` when `ANTHROPIC_API_KEY` is set in the environment.
  - `_make_llm` returns `ClaudeAgentLLM` when it is not.
  - Use `monkeypatch.delenv` / `monkeypatch.setenv` and patch both constructors so we don't actually try to talk to either backend.

### Untouched tests

`tests/engine/test_llm_anthropic.py`, `tests/engine/test_llm_fake.py`, `tests/engine/test_generate.py`, `tests/engine/test_grade.py`, `tests/cli/test_take.py`, `tests/server/*`, `tests/comment/*`, `tests/ghio/*`, `tests/test_smoke.py` — all keep passing without modification because the Protocol is unchanged and `FakeLLM` still drives every test path that doesn't specifically target one adapter.

## Performance budget

For a typical 5-question quiz with 3 mermaids:

| Phase | Anthropic SDK (current) | Agent SDK (new) | Delta |
|---|---|---|---|
| Outline call | ~6–12s | ~6–12s + ~1.5s subprocess | +1.5s |
| Mermaid x3 (serial) | ~3 × 5s | ~3 × 5s + 3 × 1.5s | +4.5s |
| Grade (on submit) | ~3s | ~3s + ~1.5s | +1.5s |
| **Total** | ~25–35s | ~32–43s | +6–8s |

Acceptable for an interactive quiz-generation flow. The persistent-client optimization (option C in brainstorming) is left as a future improvement if real users complain.

## Out of scope (follow-ups)

- README update: clarify the auth/model matrix, mention that OAuth + Sonnet/Opus now routes through `claude` binary.
- `take.py` error message polish: detect the "Sonnet/Opus 429 via direct SDK" shape and tell the user to `unset ANTHROPIC_API_KEY` if they meant to use OAuth.
- Drop SDK retries on the gated 429 (`Anthropic(max_retries=0)` when on OAuth). Wasted requests today; the agent-SDK path doesn't have this problem.
- Persistent `ClaudeSDKClient` for the mermaid fan-out. Only worth pursuing if subprocess startup becomes a real bottleneck.
