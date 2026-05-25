import json
import os
import subprocess
import sys
from pathlib import Path


def _run(tool_input: dict, root: Path | None, tool_name: str = "Read") -> dict:
    env = {"PATH": os.environ.get("PATH", "")}
    if root is not None:
        env["COGNIT_REPO_ROOT"] = str(root)
    p = subprocess.run(
        [sys.executable, "-m", "cognit.mcp.confine"],
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        capture_output=True, text=True, env=env,
    )
    return json.loads(p.stdout or "{}")


def _denied(out: dict) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_in_repo_allowed(tmp_path: Path):
    assert _run({"file_path": str(tmp_path / "a.py")}, tmp_path) == {}


def test_out_of_repo_denied(tmp_path: Path):
    assert _denied(_run({"file_path": "/etc/passwd"}, tmp_path))


def test_dotdot_escape_denied(tmp_path: Path):
    assert _denied(_run({"file_path": "../../etc/shadow"}, tmp_path))


def test_glob_pattern_escape_denied(tmp_path: Path):
    assert _denied(_run({"pattern": "../../**/*.key"}, tmp_path, tool_name="Glob"))


def test_grep_regex_pattern_not_treated_as_path(tmp_path: Path):
    # Grep's pattern is a regex; "../../old-api" must NOT be confined. path is in-repo.
    assert _run({"pattern": "../../old-api", "path": str(tmp_path)}, tmp_path, tool_name="Grep") == {}


def test_missing_repo_root_fails_closed(tmp_path: Path):
    assert _denied(_run({"file_path": str(tmp_path / "a.py")}, None))


def test_null_byte_denied(tmp_path: Path):
    assert _denied(_run({"file_path": "\x00/etc/passwd"}, tmp_path))


def test_symlink_escape_denied(tmp_path: Path):
    link = tmp_path / "out"
    try:
        link.symlink_to("/etc")
    except OSError:
        import pytest
        pytest.skip("cannot create symlink")
    assert _denied(_run({"file_path": str(link / "passwd")}, tmp_path))
