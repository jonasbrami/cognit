import asyncio
from typing import Any

import pytest

import cognit.engine.llm_claude_agent as llm_mod
from cognit.engine.llm_claude_agent import _TOOL_SUBMIT, _submit_validation_hook

VALID = "flowchart LR\n  A-->B\n  B-->C"


def _run(tool_input: dict[str, Any], on_event: Any = None) -> dict[str, Any]:
    matcher = _submit_validation_hook(on_event)
    hook = matcher.hooks[0]
    payload = {"tool_name": f"mcp__cognit__{_TOOL_SUBMIT}", "tool_input": tool_input}
    return asyncio.run(hook(payload, None, {}))


def _denied(out: dict[str, Any]) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _mermaid_q(options: dict[str, str], answer: str = "A") -> dict[str, Any]:
    return {
        "type": "mermaid",
        "id": "q1",
        "prompt": "which flow?",
        "options": options,
        "answer": answer,
    }


def test_matcher_targets_the_submit_tool() -> None:
    assert _submit_validation_hook(None).matcher == "mcp__cognit__submit_quiz"


DISTINCT = {
    "A": "flowchart LR\n  A-->B-->C",
    "B": "flowchart LR\n  A-->C-->B",
    "C": "flowchart LR\n  B-->A-->C",
    "D": "flowchart LR\n  C-->B-->A",
}


def test_valid_quiz_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run({"questions": [_mermaid_q(DISTINCT)]})
    assert out == {}


def test_identical_diagrams_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run({"questions": [_mermaid_q({"A": VALID, "B": VALID, "C": VALID, "D": VALID})]})
    assert _denied(out)
    assert "distinct" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_invalid_mermaid_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: src != "BAD")
    out = _run({"questions": [_mermaid_q({"A": "BAD", "B": VALID, "C": VALID, "D": VALID})]})
    assert _denied(out)
    assert "invalid mermaid" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_wrong_option_count_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run({"questions": [_mermaid_q({"A": VALID, "B": VALID, "C": VALID})]})
    assert _denied(out)
    assert "exactly 4" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_shape_invalid_denied() -> None:
    # missing options/answer/prompt -> QuizDraft.model_validate raises -> deny
    out = _run({"questions": [{"type": "mermaid", "id": "q1"}]})
    assert _denied(out)


def test_non_mermaid_quiz_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: True)
    out = _run(
        {
            "questions": [
                {"type": "mcq", "id": "q1", "prompt": "?", "options": ["x", "y"], "answer": "x"}
            ]
        }
    )
    assert out == {}


def test_emits_validation_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "is_valid_mermaid", lambda src, strict=False: False)
    events: list[dict[str, Any]] = []
    _run(
        {"questions": [_mermaid_q({"A": "x", "B": "x", "C": "x", "D": "x"})]},
        on_event=events.append,
    )
    texts = [e.get("text", "") for e in events]
    assert any("checking" in t for t in texts)
    assert any("fixing" in t for t in texts)
