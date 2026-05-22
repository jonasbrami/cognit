# `claude_agent_sdk` engine adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second `LLMClient` implementation, `ClaudeAgentLLM`, that routes inference through the `claude` binary via `claude_agent_sdk`. Wire it into `cli/take.py` so OAuth-only users hit the binary's session-bound credential path (and unlock sonnet/opus) instead of the direct-SDK path that Anthropic gates to haiku.

**Architecture:** New adapter is a sync class implementing the existing `LLMClient` Protocol; each method uses an in-process MCP tool (`@tool` + `create_sdk_mcp_server`) to capture the agent's structured output, hidden behind `asyncio.run`. `take.py:_make_llm` branches on `ANTHROPIC_API_KEY`: present → `AnthropicLLM`, absent → `ClaudeAgentLLM`. Errors from the SDK map to `RuntimeError`, which the existing `take.py` `except` chain already handles after a one-line addition.

**Tech Stack:** Python 3.12+, `claude-agent-sdk>=0.1.44`, pydantic, pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-05-22-claude-agent-sdk-engine-design.md`

---

## File Structure

**New files:**
- `src/quizz/engine/llm_claude_agent.py` — `ClaudeAgentLLM` class
- `tests/engine/test_llm_claude_agent.py` — unit + one integration test
- `tests/cli/test_take_select.py` — `_make_llm` selection logic

**Modified files:**
- `pyproject.toml` — add `claude-agent-sdk>=0.1.44` to runtime deps
- `src/quizz/cli/take.py` — `_make_llm` selection + `RuntimeError` in `_generate_and_post` exception clause

---

## Task 1: Add the dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list, ~line 6–13)

- [ ] **Step 1: Add the dep**

Edit `pyproject.toml`:

```toml
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "httpx>=0.27",
    "fastapi>=0.136.1",
    "uvicorn>=0.47.0",
    "anthropic>=0.102.0",
    "claude-agent-sdk>=0.1.44",
]
```

- [ ] **Step 2: Sync the lock**

Run: `uv sync`
Expected: exit 0, prints `claude-agent-sdk==0.1.x` among the resolved packages.

- [ ] **Step 3: Verify imports work**

Run: `uv run python -c "from claude_agent_sdk import query, tool, create_sdk_mcp_server, ClaudeAgentOptions, CLINotFoundError; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add claude-agent-sdk runtime dep"
```

---

## Task 2: Skeleton `ClaudeAgentLLM` (no behavior yet)

**Files:**
- Create: `src/quizz/engine/llm_claude_agent.py`

- [ ] **Step 1: Write the skeleton**

Create `src/quizz/engine/llm_claude_agent.py`:

```python
"""claude_agent_sdk-based LLM adapter for quizz.

Routes inference through the official `claude` binary (subprocessed by
claude_agent_sdk) so users on the Claude Code OAuth path can use sonnet/opus.
The direct Anthropic SDK + OAuth combo is gated by Anthropic to haiku only
(see docs/superpowers/specs/2026-05-22-claude-agent-sdk-engine-design.md).

The adapter implements the existing sync `LLMClient` Protocol by wrapping each
call in `asyncio.run`. Structured output is captured via in-process MCP tools:
the agent invokes a `submit_*` tool, the handler stuffs the validated args
into a closure-shared list, the adapter returns the args as a Pydantic model.
"""

from __future__ import annotations

import asyncio
from importlib import resources
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    create_sdk_mcp_server,
    query,
    tool,
)

from quizz.engine.llm import GenerateRequest
from quizz.engine.models import MermaidSet, MermaidSpec, QuizOutline

_TOOL_OUTLINE = "submit_quiz_outline"
_TOOL_MERMAID = "submit_mermaid_set"
_TOOL_GRADE = "submit_grade"


def _load_prompt(name: str) -> str:
    return resources.files("quizz.engine.prompts").joinpath(name).read_text()


def _format_files_blob(files: dict[str, str]) -> str:
    if not files:
        return ""
    return "\n".join(f'<file path="{p}">\n{c}\n</file>' for p, c in files.items())


def _format_misconceptions(misconceptions: list[str]) -> str:
    return "\n".join(f"- {m}" for m in misconceptions)


class ClaudeAgentLLM:
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model = model

    def _invoke_tool(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Spawn an agent, await one tool call, return the captured args or None."""
        raise NotImplementedError

    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        raise NotImplementedError

    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        raise NotImplementedError

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        raise NotImplementedError
```

- [ ] **Step 2: Verify import + type-check**

Run: `uv run python -c "from quizz.engine.llm_claude_agent import ClaudeAgentLLM; print(ClaudeAgentLLM)"`
Expected: prints the class.

Run: `uv run mypy src/quizz/engine/llm_claude_agent.py`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/quizz/engine/llm_claude_agent.py
git commit -m "feat(engine): scaffold ClaudeAgentLLM adapter skeleton"
```

---

## Task 3: Implement `_invoke_tool` (the SDK plumbing)

**Files:**
- Modify: `src/quizz/engine/llm_claude_agent.py:_invoke_tool`
- Create: `tests/engine/test_llm_claude_agent.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/engine/test_llm_claude_agent.py`:

```python
"""Tests for the ClaudeAgentLLM adapter.

We mock at two levels:
  - `claude_agent_sdk.query` for one integration test that exercises the
    full _invoke_tool plumbing (handler registration, MCP server, drain loop).
  - `ClaudeAgentLLM._invoke_tool` for the per-method tests, since the SDK
    plumbing is already covered by the integration test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from quizz.engine.llm import GenerateRequest
from quizz.engine.llm_claude_agent import ClaudeAgentLLM
from quizz.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizOutline


class _FakeAssistantMessage:
    """Minimal stand-in for the SDK's AssistantMessage — _invoke_tool just iterates."""

    def __init__(self) -> None:
        self.content: list[Any] = []


class _FakeResultMessage:
    def __init__(self) -> None:
        self.session_id: str | None = "test-session"
        self.is_error: bool = False


def _make_fake_query(
    *, canned_args: dict[str, Any] | None, tool_name_expected: str
) -> Any:
    """Build an async-generator function that simulates the agent calling our MCP tool."""

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        # The handler is registered inside an SdkMcpTool inside options.mcp_servers["quizz"].
        # We don't try to extract it via the MCP machinery — _invoke_tool's contract is
        # "after the stream ends, captured contains the args if the agent called the tool".
        # We simulate that by directly calling the tool's handler, which the SDK would do
        # when the agent invokes mcp__quizz__<tool>.
        if canned_args is not None:
            srv = options.mcp_servers["quizz"]
            tool_def = srv["tools"][0]
            assert tool_def.name == tool_name_expected, (
                f"expected tool {tool_name_expected}, got {tool_def.name}"
            )
            await tool_def.handler(canned_args)
        yield _FakeAssistantMessage()
        yield _FakeResultMessage()

    return fake_query


def test_invoke_tool_returns_captured_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration test: full _invoke_tool path with query() mocked at the SDK boundary."""
    canned = {"foo": "bar", "n": 42}
    monkeypatch.setattr(
        "quizz.engine.llm_claude_agent.query",
        _make_fake_query(canned_args=canned, tool_name_expected="my_tool"),
    )
    llm = ClaudeAgentLLM()
    result = llm._invoke_tool(
        system="sys",
        user="usr",
        tool_name="my_tool",
        tool_description="desc",
        tool_schema={"type": "object", "properties": {"foo": {"type": "string"}}},
    )
    assert result == canned


def test_invoke_tool_returns_none_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the agent chats without calling the tool, _invoke_tool returns None."""
    monkeypatch.setattr(
        "quizz.engine.llm_claude_agent.query",
        _make_fake_query(canned_args=None, tool_name_expected="my_tool"),
    )
    llm = ClaudeAgentLLM()
    result = llm._invoke_tool(
        system="sys",
        user="usr",
        tool_name="my_tool",
        tool_description="desc",
        tool_schema={"type": "object"},
    )
    assert result is None
```

Note for the SdkMcpTool/server access — the SDK exposes the server config as a dict-like with a `tools` key listing `SdkMcpTool` instances. If during implementation that turns out to be wrong, drop the `tool_name_expected` assertion in `_make_fake_query` and instead invoke the handler via whatever the actual structure is. The test's contract — "captured args come back from `_invoke_tool`" — does not depend on the exact mcp-server shape.

- [ ] **Step 2: Run the test, confirm it fails**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `_invoke_tool`**

Replace the stub in `src/quizz/engine/llm_claude_agent.py`:

```python
    def _invoke_tool(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        captured: list[dict[str, Any]] = []

        @tool(tool_name, tool_description, tool_schema)
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            captured.append(args)
            return {"content": [{"type": "text", "text": "ok"}]}

        server = create_sdk_mcp_server(name="quizz", tools=[handler])
        options = ClaudeAgentOptions(
            system_prompt=system,
            model=self._model,
            mcp_servers={"quizz": server},
            allowed_tools=[f"mcp__quizz__{tool_name}"],
            max_turns=2,
            permission_mode="bypassPermissions",
            setting_sources=[],
        )

        async def drain() -> None:
            async for _ in query(prompt=user, options=options):
                pass

        try:
            asyncio.run(drain())
        except CLINotFoundError as e:
            raise RuntimeError(
                "claude binary not found; install Claude Code "
                "(`npm i -g @anthropic-ai/claude-code`) or set ANTHROPIC_API_KEY"
            ) from e
        except (CLIConnectionError, ProcessError, ClaudeSDKError) as e:
            raise RuntimeError(f"claude agent SDK call failed: {e}") from e

        return captured[0] if captured else None
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py -v`
Expected: 2 passed.

If the fake-query's access path `options.mcp_servers["quizz"]["tools"][0]` fails: drop the `tool_def` lookup in the test, expose the handler via a module-level captured-handler hook (`monkeypatch.setattr` on a `_HANDLER_FOR_TEST` attribute set inside `_invoke_tool`), and invoke that in the fake. Don't lock the SDK's internal shape into the production code.

- [ ] **Step 5: Commit**

```bash
git add src/quizz/engine/llm_claude_agent.py tests/engine/test_llm_claude_agent.py
git commit -m "feat(engine): implement _invoke_tool via in-process MCP tool"
```

---

## Task 4: TDD `generate_quiz_outline`

**Files:**
- Modify: `src/quizz/engine/llm_claude_agent.py:generate_quiz_outline`
- Modify: `tests/engine/test_llm_claude_agent.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/engine/test_llm_claude_agent.py`:

```python
def test_generate_quiz_outline_calls_outline_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    seen: dict[str, Any] = {}

    def fake_invoke(self, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return canned.model_dump()

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    out = llm.generate_quiz_outline(
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={})
    )
    assert out == canned
    assert seen["tool_name"] == "submit_quiz_outline"
    # Schema should be the QuizOutline JSON schema, not Quiz's
    defs = seen["tool_schema"].get("$defs", {})
    assert "MermaidPlaceholder" in defs
    assert "MermaidQuestion" not in defs
    # System prompt should be the generate one
    assert "comprehension quiz author" in seen["system"].lower()
```

- [ ] **Step 2: Run, confirm fail**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py::test_generate_quiz_outline_calls_outline_tool -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement**

Replace the stub:

```python
    def generate_quiz_outline(self, req: GenerateRequest) -> QuizOutline:
        system = _load_prompt("system_generate.txt")
        user = _load_prompt("generate.txt").format(
            pr_title=req.pr_title,
            pr_body=req.pr_body,
            diff=req.diff,
            files=_format_files_blob(req.files),
        )
        args = self._invoke_tool(
            system=system,
            user=user,
            tool_name=_TOOL_OUTLINE,
            tool_description="Submit the generated quiz outline.",
            tool_schema=QuizOutline.model_json_schema(),
        )
        if args is None:
            raise RuntimeError(f"agent did not call {_TOOL_OUTLINE}")
        return QuizOutline.model_validate(args)
```

- [ ] **Step 4: Run, confirm pass**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py::test_generate_quiz_outline_calls_outline_tool -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quizz/engine/llm_claude_agent.py tests/engine/test_llm_claude_agent.py
git commit -m "feat(engine): ClaudeAgentLLM.generate_quiz_outline"
```

---

## Task 5: TDD `generate_mermaid_set`

**Files:**
- Modify: `src/quizz/engine/llm_claude_agent.py:generate_mermaid_set`
- Modify: `tests/engine/test_llm_claude_agent.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_generate_mermaid_set_calls_mermaid_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = MermaidSet(
        options={
            "A": "flowchart LR\nA-->B",
            "B": "flowchart LR\nB-->A",
            "C": "flowchart LR\nA-->C",
            "D": "flowchart LR\nD-->A",
        },
        correct="A",
    )
    seen: dict[str, Any] = {}

    def fake_invoke(self, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return canned.model_dump()

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    out = llm.generate_mermaid_set(
        MermaidSpec(
            diagram_type="flowchart",
            correct_description="A calls B",
            misconceptions=["B calls A", "no call", "extra C"],
            style_notes="2 nodes, LR",
        ),
        GenerateRequest(diff="x", pr_title="t", pr_body="b", files={}),
    )
    assert out == canned
    assert seen["tool_name"] == "submit_mermaid_set"
    assert "mermaid" in seen["system"].lower()
    # Schema enforces 4 options + a correct key
    schema = seen["tool_schema"]
    assert schema["properties"]["options"]["required"] == ["A", "B", "C", "D"]
    assert schema["properties"]["correct"]["enum"] == ["A", "B", "C", "D"]
```

- [ ] **Step 2: Run, confirm fail**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py::test_generate_mermaid_set_calls_mermaid_tool -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
    def generate_mermaid_set(self, spec: MermaidSpec, req: GenerateRequest) -> MermaidSet:
        system = _load_prompt("system_mermaid.txt")
        user = _load_prompt("mermaid.txt").format(
            diagram_type=spec.diagram_type,
            correct_description=spec.correct_description,
            misconceptions=_format_misconceptions(spec.misconceptions),
            style_notes=spec.style_notes,
        )
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "description": "Exactly four keys A, B, C, D mapping to mermaid sources.",
                    "properties": {
                        "A": {"type": "string"},
                        "B": {"type": "string"},
                        "C": {"type": "string"},
                        "D": {"type": "string"},
                    },
                    "required": ["A", "B", "C", "D"],
                    "additionalProperties": False,
                },
                "correct": {"type": "string", "enum": ["A", "B", "C", "D"]},
            },
            "required": ["options", "correct"],
            "additionalProperties": False,
        }
        args = self._invoke_tool(
            system=system,
            user=user,
            tool_name=_TOOL_MERMAID,
            tool_description="Submit 4 mermaid diagrams keyed A/B/C/D plus which is correct.",
            tool_schema=schema,
        )
        if args is None:
            raise RuntimeError(f"agent did not call {_TOOL_MERMAID}")
        return MermaidSet.model_validate(args)
```

- [ ] **Step 4: Run, confirm pass**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py::test_generate_mermaid_set_calls_mermaid_tool -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quizz/engine/llm_claude_agent.py tests/engine/test_llm_claude_agent.py
git commit -m "feat(engine): ClaudeAgentLLM.generate_mermaid_set"
```

---

## Task 6: TDD `grade_open`

**Files:**
- Modify: `src/quizz/engine/llm_claude_agent.py:grade_open`
- Modify: `tests/engine/test_llm_claude_agent.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_grade_open_calls_grade_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_invoke(self, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return {"score": 75, "feedback": "good"}

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    score, fb = llm.grade_open("why?", "must mention X", "because")
    assert (score, fb) == (75, "good")
    assert seen["tool_name"] == "submit_grade"


def test_grade_open_clamps_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Anthropic adapter clamps score to [0, 100] — match that for consistency."""

    def fake_invoke(self, **kw):  # type: ignore[no-untyped-def]
        return {"score": 150, "feedback": "fine"}

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    score, _ = llm.grade_open("why?", "r", "a")
    assert score == 100
```

- [ ] **Step 2: Run, confirm fail**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py -v -k grade_open`
Expected: 2 FAIL.

- [ ] **Step 3: Implement**

```python
    def grade_open(
        self, question_prompt: str, rubric: str, answer: str
    ) -> tuple[int, str]:
        system = _load_prompt("system_grade.txt")
        user = _load_prompt("grade_open.txt").format(
            prompt=question_prompt,
            rubric=rubric,
            answer=answer,
        )
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "feedback": {"type": "string"},
            },
            "required": ["score", "feedback"],
            "additionalProperties": False,
        }
        args = self._invoke_tool(
            system=system,
            user=user,
            tool_name=_TOOL_GRADE,
            tool_description="Submit a score and feedback for the open-ended answer.",
            tool_schema=schema,
        )
        if args is None:
            raise RuntimeError(f"agent did not call {_TOOL_GRADE}")
        score = max(0, min(100, int(args.get("score", 0))))
        return score, str(args.get("feedback", ""))
```

- [ ] **Step 4: Run, confirm pass**

Run: `uv run pytest tests/engine/test_llm_claude_agent.py -v`
Expected: all tests in file PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quizz/engine/llm_claude_agent.py tests/engine/test_llm_claude_agent.py
git commit -m "feat(engine): ClaudeAgentLLM.grade_open"
```

---

## Task 7: Error mapping — `CLINotFoundError` and "tool never called"

The "tool never called" path is already implemented in tasks 4–6 (each public method raises `RuntimeError` if `_invoke_tool` returns `None`). The `CLINotFoundError` → `RuntimeError` mapping is already implemented inside `_invoke_tool`. This task just adds explicit test coverage for those failure paths.

**Files:**
- Modify: `tests/engine/test_llm_claude_agent.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_not_found_raises_runtime_with_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_agent_sdk import CLINotFoundError

    async def fake_query(*, prompt, options):  # type: ignore[no-untyped-def]
        raise CLINotFoundError("not found")
        yield  # pragma: no cover  (make it a generator)

    monkeypatch.setattr("quizz.engine.llm_claude_agent.query", fake_query)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="claude binary not found"):
        llm._invoke_tool(
            system="s",
            user="u",
            tool_name="my_tool",
            tool_description="d",
            tool_schema={"type": "object"},
        )


def test_generate_outline_raises_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ClaudeAgentLLM, "_invoke_tool", lambda self, **kw: None
    )
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_quiz_outline"):
        llm.generate_quiz_outline(
            GenerateRequest(diff="x", pr_title="t", pr_body="b", files={})
        )
```

- [ ] **Step 2: Run, confirm pass**

The behavior was implemented in earlier tasks; these tests should pass on first run.

Run: `uv run pytest tests/engine/test_llm_claude_agent.py -v`
Expected: all PASS (now including the two new tests).

If `test_cli_not_found_raises_runtime_with_install_hint` fails because the fake-query approach doesn't work as a generator that immediately raises: rewrite it as a synchronous function that raises when called (assign it to the module attribute even though it doesn't return a generator — `asyncio.run` calls the inner coroutine, not the outer `query`). If that still doesn't work, monkeypatch `asyncio.run` instead and raise from there.

- [ ] **Step 3: Commit**

```bash
git add tests/engine/test_llm_claude_agent.py
git commit -m "test(engine): cover CLINotFoundError and missing-tool-call paths"
```

---

## Task 8: TDD `_make_llm` selection in `cli/take.py`

**Files:**
- Modify: `src/quizz/cli/take.py:_make_llm` (~line 30–32)
- Create: `tests/cli/test_take_select.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_take_select.py`:

```python
"""_make_llm picks AnthropicLLM when ANTHROPIC_API_KEY is set,
otherwise ClaudeAgentLLM. This is the only public auth signal we honor."""

from __future__ import annotations

import pytest

from quizz.cli.take import _make_llm
from quizz.engine.llm_anthropic import AnthropicLLM
from quizz.engine.llm_claude_agent import ClaudeAgentLLM


def test_make_llm_uses_anthropic_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    llm = _make_llm("claude-sonnet-4-6")
    assert isinstance(llm, AnthropicLLM)


def test_make_llm_uses_claude_agent_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = _make_llm("claude-sonnet-4-6")
    assert isinstance(llm, ClaudeAgentLLM)
```

- [ ] **Step 2: Run, confirm fail**

Run: `uv run pytest tests/cli/test_take_select.py -v`
Expected: FAIL on the second test (current `_make_llm` always returns `AnthropicLLM`).

- [ ] **Step 3: Implement**

Edit `src/quizz/cli/take.py` — add `os` import if missing, replace `_make_llm`:

```python
import os
# ... existing imports ...
from quizz.engine.llm_claude_agent import ClaudeAgentLLM

# ...

def _make_llm(model: str) -> LLMClient:
    """Pick the adapter based on the only auth signal that matters.

    `ANTHROPIC_API_KEY` set → direct Anthropic SDK (fastest, no subprocess).
    Otherwise → `claude_agent_sdk` (subprocesses the `claude` binary, which is
    the only path that unlocks sonnet/opus for OAuth-only users; see
    docs/superpowers/specs/2026-05-22-claude-agent-sdk-engine-design.md).
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM(model=model)
    return ClaudeAgentLLM(model=model)
```

- [ ] **Step 4: Run, confirm pass**

Run: `uv run pytest tests/cli/test_take_select.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Run the rest of the cli tests to make sure nothing else broke**

Run: `uv run pytest tests/cli/ -v`
Expected: all PASS. If `test_take_auto_detects` fails, it's because `_make_llm` no longer constructs an `AnthropicLLM` (which requires creds) under the test env. Inspect the test — it already monkeypatches `_make_llm`, so this should be fine.

- [ ] **Step 6: Commit**

```bash
git add src/quizz/cli/take.py tests/cli/test_take_select.py
git commit -m "feat(cli): select ClaudeAgentLLM when ANTHROPIC_API_KEY absent"
```

---

## Task 9: Wire `RuntimeError` into `take.py` error handling

**Files:**
- Modify: `src/quizz/cli/take.py:_generate_and_post` (the `except` chain around line 96–111)

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_take.py`:

```python
def test_take_handles_runtime_error_from_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClaudeAgentLLM maps SDK errors to RuntimeError; take.py must exit 1 with a message."""
    from quizz.cli.take import _run_take_flow

    class BoomLLM:
        def generate_quiz_outline(self, req):  # type: ignore[no-untyped-def]
            raise RuntimeError("claude binary not found; install Claude Code")

        def generate_mermaid_set(self, spec, req):  # type: ignore[no-untyped-def]
            raise AssertionError("should not be reached")

        def grade_open(self, *args):  # type: ignore[no-untyped-def]
            return (0, "")

    monkeypatch.setattr("quizz.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    monkeypatch.setattr(
        "quizz.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "quizz.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow(
            "https://github.com/o/r/pull/1",
            show_results_only=False,
            llm=BoomLLM(),  # type: ignore[arg-type]
        )
    assert exc_info.value.exit_code == 1
```

- [ ] **Step 2: Run, confirm fail**

Run: `uv run pytest tests/cli/test_take.py::test_take_handles_runtime_error_from_agent -v`
Expected: FAIL — the current `except` chain doesn't catch `RuntimeError`, so it bubbles up as an uncaught exception instead of a typer.Exit.

- [ ] **Step 3: Implement**

Edit `src/quizz/cli/take.py:_generate_and_post`, replace the existing `try`/`except` block:

```python
    try:
        quiz = generate_quiz(
            diff=diff,
            pr_title=info.title,
            pr_body=info.body,
            files=files,
            pr_number=info.number,
            llm=llm,
            model=model,
        )
    except AnthropicAPIError as e:
        typer.echo(f"LLM call failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from None
    except ValidationError as e:
        typer.echo(f"LLM returned malformed quiz: {e}", err=True)
        raise typer.Exit(code=1) from None
    except RuntimeError as e:
        typer.echo(f"LLM call failed: {e}", err=True)
        raise typer.Exit(code=1) from None
```

- [ ] **Step 4: Run, confirm pass**

Run: `uv run pytest tests/cli/test_take.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quizz/cli/take.py tests/cli/test_take.py
git commit -m "feat(cli): surface ClaudeAgentLLM RuntimeError as exit 1 + message"
```

---

## Task 10: Full test suite + lint pass

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 2: Type-check**

Run: `uv run mypy src`
Expected: PASS (or unchanged warning count from before this work).

- [ ] **Step 3: Lint**

Run: `uv run ruff check src tests`
Expected: PASS.

- [ ] **Step 4: Format**

Run: `uv run ruff format src tests`
Expected: no changes (or only formatting-only changes; review the diff).

- [ ] **Step 5: Commit any formatting fixes**

```bash
git add -A
git diff --staged --stat
# if there are changes:
git commit -m "chore: ruff format"
```

---

## Task 11: Manual smoke test on PR #5

This validates the full end-to-end path with a real `claude` binary subprocess.

- [ ] **Step 1: Clear PR #5's quiz comment so generation re-runs**

```bash
gh api repos/jonasbrami/quizz/issues/5/comments --jq '.[] | select(.body | startswith("<!-- quizz:quiz v1 -->")) | .id' \
  | xargs -I {} gh api -X DELETE repos/jonasbrami/quizz/issues/{}/comments  # NOTE: gh api path for deleting an issue comment is /repos/.../issues/comments/{id} — adjust if the above shape is wrong
```

(If that one-liner doesn't fit your gh version, do it through the web UI or `gh pr view 5 --json comments` + `gh api -X DELETE /repos/jonasbrami/quizz/issues/comments/<id>`.)

- [ ] **Step 2: Confirm no API key in env**

Run: `echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-<unset>}"`
Expected: `<unset>`. If a key is set, run `unset ANTHROPIC_API_KEY` first.

- [ ] **Step 3: Install the worktree's quizz into the venv used for runs**

Run: `uv pip install -e .`
Expected: installs in editable mode.

- [ ] **Step 4: Run `quizz take` against PR #5 with sonnet**

Run: `BROWSER=true timeout 180 quizz take --pr https://github.com/jonasbrami/quizz/pull/5 --model claude-sonnet-4-6 2>&1 | tee /tmp/quizz-sonnet-agent.log`
Expected: `generating quiz from diff...`, then `quiz comment posted to PR.`, then `opening http://127.0.0.1:...`. Exit on timeout (the server hangs by design until Ctrl-C).

- [ ] **Step 5: Repeat with opus**

(Clear the quiz comment first, same as Step 1.)
Run: `BROWSER=true timeout 180 quizz take --pr https://github.com/jonasbrami/quizz/pull/5 --model claude-opus-4-7 2>&1 | tee /tmp/quizz-opus-agent.log`
Expected: same shape — generation succeeds, comment posted, server starts.

- [ ] **Step 6: If both succeed: commit a note + announce ready**

```bash
git log --oneline origin/main..HEAD
# Sanity-check the commit list reads as a coherent PR
```

If anything fails: re-enter the systematic-debugging loop. Do NOT add more code without root-cause investigation.

---

## Self-Review

Spec coverage:

- ✅ Two adapters side-by-side — Task 2 scaffolds `ClaudeAgentLLM`, `AnthropicLLM` untouched
- ✅ Selection in `_make_llm` on `ANTHROPIC_API_KEY` — Task 8
- ✅ Three method recipes via in-process MCP tools — Tasks 3–6
- ✅ Error mapping (`CLINotFoundError` → `RuntimeError`, tool-not-called → `RuntimeError`) — Tasks 3 & 7
- ✅ `take.py` catches `RuntimeError` — Task 9
- ✅ Existing tests untouched — verified in Tasks 8 (cli) & 10 (full suite)
- ✅ Dep added — Task 1
- ✅ Smoke test against real PR — Task 11

Type consistency:
- `_invoke_tool` returns `dict[str, Any] | None`. Per-method callers handle `None` by raising `RuntimeError`. Public methods return their Pydantic models or `tuple[int, str]` (for `grade_open`).
- Schema params are `dict[str, Any]` throughout.
- Test mocks use the same kwargs as the real `_invoke_tool` signature.

Placeholder scan: no TBD, no "implement later", no "handle edge cases" — all code blocks are complete and runnable. ✅
