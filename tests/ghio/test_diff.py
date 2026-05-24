import os
import stat

import pytest

from cognit.ghio.diff import (
    _diff_git_target_path,
    _filter_diff,
    fetch_pr_diff,
    split_diff,
    summarize_diff,
)

_TWO_FILE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-old\n+new\n+extra\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-x\n+y\n"
)


@pytest.fixture
def fake_gh_pr_diff(tmp_path, monkeypatch):
    """Fake `gh` that emits a multi-file unified diff including a vendored bundle,
    a lockfile, and a real source file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(
        """#!/bin/sh
# only `gh pr diff <num>` is exercised here
cat <<'EOF'
diff --git a/cache.py b/cache.py
index 1111111..2222222 100644
--- a/cache.py
+++ b/cache.py
@@ -1 +1 @@
-old
+new
diff --git a/assets/vendor.min.js b/assets/vendor.min.js
index 3333333..4444444 100644
--- a/assets/vendor.min.js
+++ b/assets/vendor.min.js
@@ -1 +1 @@
-var a=1
+var a=1;var b=2
diff --git a/uv.lock b/uv.lock
index 5555555..6666666 100644
--- a/uv.lock
+++ b/uv.lock
@@ -1 +1 @@
-x = 1
+x = 2
diff --git a/src/app.py b/src/app.py
index 7777777..8888888 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-def f(): pass
+def f(): return 1
EOF
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])


def test_fetch_pr_diff_keeps_real_source_files(fake_gh_pr_diff):
    diff = fetch_pr_diff("42")
    assert "+++ b/cache.py" in diff
    assert "+def f(): return 1" in diff


def test_fetch_pr_diff_strips_vendored_minified_and_lockfiles(fake_gh_pr_diff):
    """Vendored/minified/lockfile sections must be dropped wholesale so a megabytes-wide
    minified diff line can't blow the agent's context window."""
    diff = fetch_pr_diff("42")
    assert "vendor.min.js" not in diff
    assert "var a=1;var b=2" not in diff
    assert "uv.lock" not in diff


def test_fetch_pr_diff_preserves_section_order(fake_gh_pr_diff):
    diff = fetch_pr_diff("42")
    assert diff.index("cache.py") < diff.index("src/app.py")


def test_diff_git_target_path_handles_spaces_and_renames():
    # Paths with spaces are not truncated, renames return the b/ (target) path.
    assert _diff_git_target_path("diff --git a/a file.txt b/a file.txt") == "a file.txt"
    assert _diff_git_target_path("diff --git a/old.txt b/new.txt") == "new.txt"
    assert _diff_git_target_path("diff --git a/src/app.py b/src/app.py") == "src/app.py"


def test_filter_diff_strips_file_renamed_into_a_minified_path():
    """A rename whose *target* is denylisted (b/ side) must be dropped wholesale."""
    diff = (
        "diff --git a/app.js b/vendor.min.js\n"
        "similarity index 100%\n"
        "rename from app.js\n"
        "rename to vendor.min.js\n"
        "diff --git a/keep.py b/keep.py\n"
        "--- a/keep.py\n+++ b/keep.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    out = _filter_diff(diff)
    assert "vendor.min.js" not in out
    assert "keep.py" in out


def test_filter_diff_keeps_source_file_with_space_in_name():
    """A real source file with a space in its name must NOT be wrongly stripped."""
    diff = "diff --git a/my notes.py b/my notes.py\n--- a/my notes.py\n+++ b/my notes.py\n@@ -1 +1 @@\n-a\n+b\n"
    out = _filter_diff(diff)
    assert "my notes.py" in out


def test_split_diff_keys_sections_by_target_path():
    sections = split_diff(_TWO_FILE_DIFF)
    assert set(sections) == {"src/a.py", "src/b.py"}
    assert sections["src/a.py"].startswith("diff --git a/src/a.py")
    assert "+extra" in sections["src/a.py"]
    assert "+extra" not in sections["src/b.py"]  # sections don't bleed into each other


def test_split_diff_empty_is_empty():
    assert split_diff("") == {}


def test_summarize_diff_reports_per_file_counts():
    summary = summarize_diff(_TWO_FILE_DIFF)
    assert "src/a.py | +2 -1" in summary
    assert "src/b.py | +1 -1" in summary
    # A summary, not the hunks — no diff body leaks in.
    assert "@@" not in summary
    assert "+extra" not in summary


def test_summarize_diff_empty():
    assert summarize_diff("") == "(no changed files after filtering)"
