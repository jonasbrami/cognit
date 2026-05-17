import json
import os
import stat
import pytest
from quizz.ghio.pr import fetch_pr_info, PRInfo


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """Place a fake `gh` on PATH that returns canned JSON."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    payload = {
        "number": 42,
        "title": "Add caching",
        "body": "Adds an in-memory cache.",
        "headRepository": {"nameWithOwner": "acme/repo"},
        "headRefName": "feat/cache",
        "author": {"login": "alice"},
    }
    fake.write_text(f"#!/bin/sh\necho '{json.dumps(payload)}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return payload


def test_fetch_pr_info(fake_gh):
    info = fetch_pr_info("https://github.com/acme/repo/pull/42")
    assert info == PRInfo(
        number=42,
        title="Add caching",
        body="Adds an in-memory cache.",
        repo="acme/repo",
        branch="feat/cache",
        author="alice",
    )


def test_find_quiz_comment(monkeypatch):
    from quizz.ghio.pr import find_latest_marker_comment

    monkeypatch.setattr(
        "quizz.ghio.pr.list_comments",
        lambda pr: [
            {"body": "drive-by", "createdAt": "2026-01-01T00:00:00Z"},
            {
                "body": "<!-- quizz:quiz v1 -->\n```json\n{...}\n```",
                "createdAt": "2026-01-02T00:00:00Z",
            },
            {"body": "<!-- quizz:quiz v1 -->\nnewer", "createdAt": "2026-01-03T00:00:00Z"},
        ],
    )
    c = find_latest_marker_comment("123", "<!-- quizz:quiz v1 -->")
    assert c is not None
    assert "newer" in c


def test_find_returns_none_when_no_match(monkeypatch):
    from quizz.ghio.pr import find_latest_marker_comment

    monkeypatch.setattr(
        "quizz.ghio.pr.list_comments",
        lambda pr: [{"body": "no marker here", "createdAt": "2026-01-01T00:00:00Z"}],
    )
    assert find_latest_marker_comment("123", "<!-- quizz:quiz v1 -->") is None
