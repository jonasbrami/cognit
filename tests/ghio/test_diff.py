import os
import stat
import pytest
from cognit.ghio.diff import fetch_diff_and_files


@pytest.fixture
def fake_gh_diff(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    # `gh pr diff <num>` → echo a fixed diff
    # `gh pr view <num> --json files` → echo a fixed files JSON
    fake.write_text("""#!/bin/sh
case "$2" in
  diff) echo '--- a/cache.py
+++ b/cache.py
@@ -1 +1 @@
-old
+new' ;;
  view)
    if echo "$@" | grep -q files; then
      echo '{"files":[{"path":"cache.py"}]}'
    else
      echo '{}'
    fi ;;
esac
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])


def test_diff_returns_unified_diff(fake_gh_diff):
    diff, files = fetch_diff_and_files("42", fetch_file_contents=lambda path: "fake content")
    assert "+++ b/cache.py" in diff
    assert files == {"cache.py": "fake content"}
