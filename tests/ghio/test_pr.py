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
