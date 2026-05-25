import pytest
from typer.testing import CliRunner

from cognit.cli import app
from cognit.ghio.pr import PRInfo

runner = CliRunner()


def test_take_errors_when_no_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cognit.cli.take._detect_pr_from_branch", lambda: None)
    result = runner.invoke(app, ["take"])
    assert result.exit_code != 0
    assert "no pr" in result.stdout.lower()


def test_take_auto_detects(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() auto-detects PR, preflights claude, and os.execvpe's the confined session."""
    monkeypatch.setattr(
        "cognit.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/42",
    )
    monkeypatch.setattr("cognit.cli.take.shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(42, "t", "b", "o/r", "feat/x", "alice"),
    )
    # Stub git rev-parse
    monkeypatch.setattr(
        "cognit.cli.take.subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": "/repo\n", "returncode": 0})(),
    )
    monkeypatch.setattr("cognit.cli.take._load_host_prompt", lambda: "SYS")

    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execvpe(file: str, argv: list[str], env: dict[str, str]) -> None:
        exec_calls.append((file, argv, env))

    monkeypatch.setattr("cognit.cli.take.os.execvpe", fake_execvpe)

    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0, result.output
    assert len(exec_calls) == 1
    file, argv, env = exec_calls[0]
    assert file == "claude"
    assert argv[0] == "claude"
    assert "--strict-mcp-config" in argv
    assert "--permission-mode" in argv and "bypassPermissions" in argv
    assert env["COGNIT_PR_URL"] == "https://github.com/o/r/pull/42"
    assert env["COGNIT_PR_NUMBER"] == "42"


def test_take_missing_claude_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() exits with an error if `claude` binary is not found."""
    monkeypatch.setattr(
        "cognit.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/42",
    )
    monkeypatch.setattr("cognit.cli.take.shutil.which", lambda _: None)
    result = runner.invoke(app, ["take"])
    assert result.exit_code != 0
    assert "claude" in result.output.lower()


def test_take_run_respects_quiz_skip_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() returns early (no exec) when PR body contains 'quiz: skip'."""
    monkeypatch.setattr(
        "cognit.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/1",
    )
    monkeypatch.setattr("cognit.cli.take.shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        "cognit.cli.take.fetch_pr_info",
        lambda pr: PRInfo(1, "t", "quiz: skip\n\nThis PR...", "o/r", "br", "alice"),
    )
    exec_calls: list[object] = []
    monkeypatch.setattr(
        "cognit.cli.take.os.execvpe",
        lambda *a: exec_calls.append(a),
    )
    result = runner.invoke(app, ["take"])
    assert result.exit_code == 0, result.output
    assert exec_calls == [], "should not exec when quiz: skip is in body"
    assert "skip" in result.output.lower()


def test_take_show_results_no_comment_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """--show-results exits 1 with a message when no results comment exists on the PR."""
    monkeypatch.setattr(
        "cognit.cli.take._detect_pr_from_branch",
        lambda: "https://github.com/o/r/pull/7",
    )
    monkeypatch.setattr(
        "cognit.cli.take.find_latest_marker_comment",
        lambda pr, marker: None,
    )
    exec_calls: list[object] = []
    monkeypatch.setattr("cognit.cli.take.os.execvpe", lambda *a: exec_calls.append(a))

    result = runner.invoke(app, ["take", "--show-results"])
    assert result.exit_code != 0
    assert "no results" in result.output.lower()
    assert exec_calls == [], "os.execvpe must not be called for --show-results"
