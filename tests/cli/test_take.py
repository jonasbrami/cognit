import hashlib
import tempfile
from pathlib import Path

import httpx
import pytest
import typer
from anthropic import APIError as AnthropicAPIError
from typer.testing import CliRunner

from cognit.cli import app
from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, QuizOutline
from cognit.ghio.pr import PRInfo

runner = CliRunner()


def _fake_llm() -> FakeLLM:
    return FakeLLM(canned_open_score=80, canned_open_feedback="ok")


def _fake_llm_with_outline() -> FakeLLM:
    return FakeLLM(
        canned_outline=QuizOutline(
            questions=[MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A")],
        ),
        canned_open_score=80,
        canned_open_feedback="ok",
    )


def _cache_path(pr_url: str) -> Path:
    """Mirror of cli.take._cache_path_for, used to clean up in tests."""
    digest = hashlib.sha1(pr_url.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "cognit" / f"{digest}.json"


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """Each test gets a fresh cache. Cleans up any leftover files in $TMPDIR/cognit/."""
    cache_dir = Path(tempfile.gettempdir()) / "cognit"
    if cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)


def test_take_errors_when_no_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cognit.cli.take._detect_pr_from_branch", lambda: None)
    result = runner.invoke(app, ["take"])
    assert result.exit_code != 0
    assert "no pr" in result.stdout.lower()


def test_take_auto_detects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cognit.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/42",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "cognit.cli.take._run_take_flow",
        lambda pr_url, show_results_only, llm, **kw: captured.update(
            {"pr": pr_url, "show": show_results_only, "llm": llm, **kw}
        ),
    )
    monkeypatch.setattr("cognit.cli.take._make_llm", lambda model: _fake_llm())
    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0, result.stdout
    assert captured["pr"] == "https://github.com/o/r/pull/42"
    assert captured["show"] is False


def test_take_generates_and_does_not_post_to_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-generation must NOT post the quiz to the PR. The quiz lives in memory + cache."""
    from cognit.cli.take import _run_take_flow

    pr_url = "https://github.com/o/r/pull/42"
    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("diffstr\n" * 100, {}),
    )
    posted: list[str] = []
    monkeypatch.setattr(
        "cognit.cli.take.post_comment",
        lambda pr, md: posted.append(md) or "https://x/y#1",
    )
    served: dict[str, object] = {}

    def fake_serve(quiz_, pr_url_, llm, post_comment_fn):  # type: ignore[no-untyped-def]
        served["quiz"] = quiz_
        served["pr_url"] = pr_url_

    monkeypatch.setattr("cognit.cli.take._serve_blocking", fake_serve)

    _run_take_flow(pr_url, show_results_only=False, llm=_fake_llm_with_outline())

    # The quiz should have been generated and served, but NEVER posted to the PR.
    assert served["pr_url"] == pr_url
    assert posted == [], "auto-generation must not post to the PR thread"
    # Cache file should exist.
    assert _cache_path(pr_url).exists()


def test_take_reuses_cache_on_second_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second invocation against the same PR should reuse the cached quiz, no LLM call."""
    from cognit.cli.take import _run_take_flow

    pr_url = "https://github.com/o/r/pull/42"

    # First run: generate and cache.
    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("diffstr\n" * 100, {}),
    )
    monkeypatch.setattr("cognit.cli.take.post_comment", lambda pr, md: "https://x/y#1")
    monkeypatch.setattr("cognit.cli.take._serve_blocking", lambda *a, **k: None)

    _run_take_flow(pr_url, show_results_only=False, llm=_fake_llm_with_outline())
    assert _cache_path(pr_url).exists()

    # Second run: must NOT call fetch_pr_info or fetch_diff (cache wins).
    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("should not be called on cache hit")

    monkeypatch.setattr("cognit.cli.take.fetch_pr_info", boom)
    monkeypatch.setattr("cognit.cli.take.fetch_diff_and_files", boom)

    _run_take_flow(pr_url, show_results_only=False, llm=_fake_llm_with_outline())


def test_take_show_results_when_no_results_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    from cognit.cli.take import _run_take_flow

    monkeypatch.setattr("cognit.cli.take.find_latest_marker_comment", lambda pr, marker: None)

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow("https://github.com/o/r/pull/42", show_results_only=True, llm=_fake_llm())
    assert exc_info.value.exit_code == 1


def test_take_skips_small_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    from cognit.cli.take import _run_take_flow

    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("only one line\n", {}),
    )

    def fail_serve(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("should not serve when diff is too small")

    monkeypatch.setattr("cognit.cli.take._serve_blocking", fail_serve)

    _run_take_flow(
        "https://github.com/o/r/pull/1", show_results_only=False, llm=_fake_llm_with_outline()
    )
    # No cache should be written for skipped PRs.
    assert not _cache_path("https://github.com/o/r/pull/1").exists()


def test_take_respects_quiz_skip_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from cognit.cli.take import _run_take_flow

    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "quiz: skip\n\nThis PR ...", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )

    def fail_serve(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("should not serve when quiz: skip is in body")

    monkeypatch.setattr("cognit.cli.take._serve_blocking", fail_serve)

    _run_take_flow(
        "https://github.com/o/r/pull/1", show_results_only=False, llm=_fake_llm_with_outline()
    )


def test_take_handles_llm_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM failure during auto-generation should exit 1 with a friendly message."""
    from cognit.cli.take import _run_take_flow

    class BoomLLM:
        def generate_quiz_outline(self, req):  # type: ignore[no-untyped-def]
            raise AnthropicAPIError(
                message="simulated network failure",
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                body=None,
            )

        def generate_mermaid_set(self, spec, req):  # type: ignore[no-untyped-def]
            raise AssertionError("should not be reached")

        def grade_open(self, *args):  # type: ignore[no-untyped-def]
            return (0, "")

    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow(
            "https://github.com/o/r/pull/1",
            show_results_only=False,
            llm=BoomLLM(),  # type: ignore[arg-type]
        )
    assert exc_info.value.exit_code == 1


def test_take_handles_runtime_error_from_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClaudeAgentLLM maps SDK errors to RuntimeError; take.py must exit 1 with a message."""
    from cognit.cli.take import _run_take_flow

    class BoomLLM:
        def generate_quiz_outline(self, req):  # type: ignore[no-untyped-def]
            raise RuntimeError("claude binary not found; install Claude Code")

        def generate_mermaid_set(self, spec, req):  # type: ignore[no-untyped-def]
            raise AssertionError("should not be reached")

        def grade_open(self, *args):  # type: ignore[no-untyped-def]
            return (0, "")

    monkeypatch.setattr("cognit.cli.take.find_latest_marker_comment", lambda pr, marker: None)
    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "b", "o/r", "br", "alice"),
    )
    monkeypatch.setattr(
        "cognit.cli.take.fetch_diff_and_files",
        lambda pr, fetch_file_contents=None: ("a\n" * 100, {}),
    )

    with pytest.raises(typer.Exit) as exc_info:
        _run_take_flow(
            "https://github.com/o/r/pull/1",
            show_results_only=False,
            llm=BoomLLM(),  # type: ignore[arg-type]
        )
    assert exc_info.value.exit_code == 1
