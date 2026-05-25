import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="claude not installed")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_agent_generates_quiz_from_diff(tmp_path: Path):
    repo = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                          text=True, check=True).stdout.strip()
    snapshot = tmp_path / "snap.json"
    mcp_cfg = tmp_path / "mcp.json"
    mcp_cfg.write_text(json.dumps({"mcpServers": {"cognit": {
        "command": sys.executable, "args": ["-m", "cognit.mcp"]}}}))
    sys_prompt = Path(repo, "src/cognit/engine/prompts/system_generate.txt").read_text()
    env = {**os.environ,
           "COGNIT_PR_URL": "DUMMY", "COGNIT_PR_NUMBER": "0",
           "COGNIT_HTTP_PORT": str(_free_port()), "COGNIT_SNAPSHOT_PATH": str(snapshot),
           "COGNIT_REPO_ROOT": repo}
    diff = subprocess.run(["git", "diff", "main...HEAD", "--", "src/cognit/mcp/state.py"],
                          capture_output=True, text=True, cwd=repo).stdout[:6000]
    kickoff = ("Build a 2-question quiz about this diff and render it with set_quiz; do NOT "
               f"call changed_files/file_diff. Then stop.\n\n<diff>\n{diff}\n</diff>")
    proc = subprocess.run(
        ["claude", "-p", kickoff, "--mcp-config", str(mcp_cfg), "--strict-mcp-config",
         "--append-system-prompt", sys_prompt, "--permission-mode", "bypassPermissions",
         "--tools", "Read Grep Glob", "--model", "sonnet"],
        env=env, capture_output=True, text=True, timeout=240, cwd=repo,
    )
    assert snapshot.exists(), f"no snapshot written. claude stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-1000:]}"
    data = json.loads(snapshot.read_text())
    assert data["quiz"] is not None, f"quiz not set. claude said:\n{proc.stdout[-2000:]}"
    assert len(data["quiz"]["questions"]) >= 1
