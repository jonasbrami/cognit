"""Tests for the ClaudeAgentLLM adapter.

Mocking seam: `ClaudeAgentLLM._drain_agent`. Production's `_drain_agent` ignores
its `handler` argument (the SDK fires the registered MCP handler internally); tests
override it to inject canned tool-call args without spawning a real `claude`
subprocess. The `options` it receives let tests assert the exact tool restrictions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_agent_sdk import CLINotFoundError

import cognit.engine.llm_claude_agent as llm_mod
from cognit.engine.llm import GenerateRequest
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.engine.models import MCQQuestion, MermaidSet, MermaidSpec, QuizOutline


def _req() -> GenerateRequest:
    return GenerateRequest(
        pr_title="t",
        pr_body="b",
        pr_number=7,
        pr_url="https://github.com/o/r/pull/7",
        branch="feat/x",
    )


def _make_drain_that_calls_handler(args: dict[str, Any]) -> Any:
    def fake(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        asyncio.run(handler(args))

    return fake


def _make_drain_that_does_nothing() -> Any:
    def fake(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        return None

    return fake


# --- _invoke_tool (single-tool path: mermaid + grading) ---


def test_invoke_tool_returns_captured_args(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = {"foo": "bar", "n": 42}
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_calls_handler(canned))
    llm = ClaudeAgentLLM()
    result = llm._invoke_tool(
        system="sys",
        user="usr",
        tool_name="my_tool",
        tool_description="desc",
        tool_schema={"type": "object", "properties": {"foo": {"type": "string"}}},
    )
    assert result == canned


def test_invoke_tool_returns_none_when_handler_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_does_nothing())
    llm = ClaudeAgentLLM()
    result = llm._invoke_tool(
        system="sys",
        user="usr",
        tool_name="my_tool",
        tool_description="desc",
        tool_schema={"type": "object"},
    )
    assert result is None


def test_invoke_tool_disables_all_builtin_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mermaid/grading agents need only their one MCP submit tool. `tools=[]` disables
    ALL built-in tools (Bash/Write/Edit/...) — load-bearing because permission_mode is
    bypassPermissions, which would otherwise auto-approve every built-in tool."""
    captured_options: list[Any] = []

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        captured_options.append(options)

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    llm = ClaudeAgentLLM(model="claude-opus-4-7")
    llm._invoke_tool(
        system="my-system",
        user="my-user",
        tool_name="my_tool",
        tool_description="my desc",
        tool_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
    )
    assert len(captured_options) == 1
    opts = captured_options[0]
    assert opts.system_prompt == "my-system"
    assert opts.model == "claude-opus-4-7"
    assert opts.tools == []  # the real restriction
    assert opts.allowed_tools == ["mcp__cognit__my_tool"]
    assert "cognit" in opts.mcp_servers
    assert opts.permission_mode == "bypassPermissions"


def test_invoke_tool_maps_cli_not_found_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        raise CLINotFoundError("claude binary not on PATH")

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="claude binary not found"):
        llm._invoke_tool(
            system="s",
            user="u",
            tool_name="t",
            tool_description="d",
            tool_schema={"type": "object"},
        )


# --- generate_quiz_outline (agentic, read-only multi-tool path) ---


def test_generate_quiz_outline_restricts_to_readonly_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outline agent gets read-only built-ins via `tools=` (the availability knob),
    a cwd at the repo root, and a higher turn budget. It must NOT be able to write/shell."""
    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )
    seen: dict[str, Any] = {}

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        seen["options"] = options
        seen["prompt"] = prompt
        asyncio.run(handler(canned.model_dump()))  # agent submits the outline

    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo/root")

    llm = ClaudeAgentLLM()
    out = llm.generate_quiz_outline(_req())

    assert out == canned
    opts = seen["options"]
    assert opts.tools == ["Read", "Grep", "Glob"]  # only read-only built-ins available
    assert opts.allowed_tools == [
        "Read",
        "Grep",
        "Glob",
        "mcp__cognit__pr_diff",
        "mcp__cognit__submit_quiz_outline",
    ]
    assert opts.cwd == "/repo/root"
    assert opts.max_turns == 30
    assert opts.permission_mode == "bypassPermissions"
    # PR context reaches the prompt.
    assert "feat/x" in seen["prompt"]


def test_generate_quiz_outline_registers_pr_diff_and_submit_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two MCP tools must be registered: `pr_diff` (fetches the diff) and the terminal
    `submit_quiz_outline`. The `pr_diff` handler delegates to `fetch_pr_diff`."""
    recorded: dict[str, Any] = {}
    real_create = llm_mod.create_sdk_mcp_server

    def spy_create(*args: Any, **kwargs: Any) -> Any:
        recorded["tools"] = kwargs["tools"]
        return real_create(*args, **kwargs)

    canned = QuizOutline(
        questions=[MCQQuestion(id="q1", prompt="?", options=["A", "B"], answer="A")]
    )

    def fake_drain(self: ClaudeAgentLLM, *, prompt: str, options: Any, handler: Any) -> None:
        asyncio.run(handler(canned.model_dump()))

    monkeypatch.setattr(llm_mod, "create_sdk_mcp_server", spy_create)
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", fake_drain)
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(llm_mod, "fetch_pr_diff", lambda url: f"DIFF-FOR::{url}")

    llm = ClaudeAgentLLM()
    out = llm.generate_quiz_outline(_req())
    assert out == canned

    tools = recorded["tools"]
    assert [t.name for t in tools] == ["pr_diff", "submit_quiz_outline"]

    # The pr_diff tool, when invoked by the agent, returns the (filtered) diff text.
    pr_diff_tool = next(t for t in tools if t.name == "pr_diff")
    result = asyncio.run(pr_diff_tool.handler({}))
    assert result["content"][0]["text"] == "DIFF-FOR::https://github.com/o/r/pull/7"


def test_generate_quiz_outline_raises_when_submit_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_drain_agent", _make_drain_that_does_nothing())
    monkeypatch.setattr(llm_mod, "_repo_root", lambda: "/repo")
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_quiz_outline"):
        llm.generate_quiz_outline(_req())


# --- generate_mermaid_set ---


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

    def fake_invoke(self: ClaudeAgentLLM, **kw: Any) -> dict[str, Any]:
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
        _req(),
    )
    assert out == canned
    assert seen["tool_name"] == "submit_mermaid_set"
    assert "mermaid" in seen["system"].lower()
    schema = seen["tool_schema"]
    assert schema["properties"]["options"]["required"] == ["A", "B", "C", "D"]
    assert schema["properties"]["correct"]["enum"] == ["A", "B", "C", "D"]


def test_generate_mermaid_set_raises_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", lambda self, **kw: None)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_mermaid_set"):
        llm.generate_mermaid_set(
            MermaidSpec(
                diagram_type="flowchart",
                correct_description="x",
                misconceptions=["a", "b", "c"],
                style_notes="n",
            ),
            _req(),
        )


# --- grade_open ---


def test_grade_open_calls_grade_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_invoke(self: ClaudeAgentLLM, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"score": 75, "feedback": "good"}

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    score, fb = llm.grade_open("why?", "must mention X", "because")
    assert (score, fb) == (75, "good")
    assert seen["tool_name"] == "submit_grade"


def test_grade_open_clamps_score(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_invoke(self: ClaudeAgentLLM, **kw: Any) -> dict[str, Any]:
        return {"score": 150, "feedback": "fine"}

    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", fake_invoke)
    llm = ClaudeAgentLLM()
    score, _ = llm.grade_open("why?", "r", "a")
    assert score == 100


def test_grade_open_raises_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ClaudeAgentLLM, "_invoke_tool", lambda self, **kw: None)
    llm = ClaudeAgentLLM()
    with pytest.raises(RuntimeError, match="submit_grade"):
        llm.grade_open("why?", "r", "a")
