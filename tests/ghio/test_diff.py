import os
import stat

import pytest

from cognit.ghio.diff import fetch_pr_diff


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
