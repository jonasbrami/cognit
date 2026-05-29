"""Pure construction of the confined `claude` launch (argv + env + config file contents).

Keeping this pure makes the launch unit-testable; the CLI writes the config files and
calls os.execvpe. The session is confined exactly like the SDK generation agent:
read-only built-in tools, strict MCP, branch settings ignored, plus a read-confinement
PreToolUse hook."""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LaunchSpec:
    argv: list[str]
    env: dict[str, str]
    mcp_config_json: str
    settings_json: str


def _kickoff(pr_number: int, branch: str) -> str:
    return (
        f"Generate a comprehension quiz for PR #{pr_number} on branch `{branch}`. "
        "Call `changed_files`, then `file_diff(path)` for the files worth quizzing "
        "(read surrounding context with Read/Grep/Glob). Render it with `set_quiz`. "
        "Then wait — the reader takes the quiz in the browser and will ask you here to "
        "skip/replace questions, make them harder, or grade them."
    )


def _resume_kickoff(pr_number: int, branch: str) -> str:
    return (
        f"A quiz for PR #{pr_number} on branch `{branch}` already exists and is shown "
        "in the reader's browser. Do NOT regenerate it. Wait for the reader to ask you "
        "to skip/replace questions, make them harder, focus a file, or grade them — and "
        "use the tools (replace_question / set_quiz / grade) when they do."
    )


def build_launch_spec(
    *,
    pr_url: str,
    pr_number: int,
    branch: str,
    port: int,
    snapshot_path: Path,
    repo_root: Path,
    mcp_config_path: Path,
    settings_path: Path,
    system_prompt: str,
    model: str,
    resume: bool = False,
    debug_log: Path | None = None,
) -> LaunchSpec:
    py = sys.executable
    mcp_config = {"mcpServers": {"cognit": {"command": py, "args": ["-m", "cognit.mcp"]}}}
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Read|Grep|Glob",
                    "hooks": [
                        {"type": "command", "command": f"{shlex.quote(py)} -m cognit.mcp.confine"},
                    ],
                }
            ]
        }
    }
    env = {
        "COGNIT_PR_URL": pr_url,
        "COGNIT_PR_NUMBER": str(pr_number),
        "COGNIT_HTTP_PORT": str(port),
        "COGNIT_SNAPSHOT_PATH": str(snapshot_path),
        "COGNIT_REPO_ROOT": str(repo_root),
        "COGNIT_BRANCH": branch,  # so the browser can deep-link anchors to files on this branch
    }
    argv = [
        "claude",
        "--model",
        model,
        "--tools",
        "Read Grep Glob",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path),
        "--settings",
        str(settings_path),
        "--setting-sources",
        "user",
        "--permission-mode",
        "bypassPermissions",
    ]
    # --debug-file persists claude's own debug stream (incl. MCP traffic + tool errors)
    # without flooding the interactive terminal. Opt-in via COGNIT_LOG_LEVEL=DEBUG.
    if debug_log is not None:
        argv += ["--debug-file", str(debug_log)]
    argv += [
        "--append-system-prompt",
        system_prompt,
        # The trailing positional is claude's initial prompt — must stay last.
        _resume_kickoff(pr_number, branch) if resume else _kickoff(pr_number, branch),
    ]
    return LaunchSpec(
        argv=argv,
        env=env,
        mcp_config_json=json.dumps(mcp_config),
        settings_json=json.dumps(settings),
    )
