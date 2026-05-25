"""Claude Code PreToolUse command hook: confine Read/Grep/Glob to the repo root.

Reads the PreToolUse JSON on stdin; denies any filesystem-path argument that resolves
outside `COGNIT_REPO_ROOT`. Runnable as `python -m cognit.mcp.confine`. Fail-CLOSED:
a missing repo root or an unparseable path denies the call.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Filesystem-path arguments, always confined. NOTE: Glob's `pattern` is a path-glob
# (confined below when tool_name == "Glob"), but Grep's `pattern` is a REGEX — never
# treat that as a path, or legitimate greps for strings containing "../" get denied.
_FS_KEYS = ("file_path", "path", "notebook_path")


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    sys.exit(0)


def main() -> None:
    root_env = os.environ.get("COGNIT_REPO_ROOT", "")
    if not root_env.strip():
        # Fail closed: without a known repo root we cannot safely allow any read.
        _deny("COGNIT_REPO_ROOT is not set; refusing reads without a known repo root.")
    root = Path(root_env).resolve()
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        # Unparseable envelope is a Claude-side integration error, not an attacker vector
        # (the attacker controls tool_input content, not the envelope). Log + allow.
        print(f"confine: malformed stdin, allowing: {e}", file=sys.stderr)
        sys.exit(0)
    tool_input = data.get("tool_input") or {}
    keys = list(_FS_KEYS)
    if data.get("tool_name") == "Glob":
        keys.append("pattern")  # Glob.pattern is a path-glob; confine it. (Grep.pattern is regex.)
    try:
        for key in keys:
            raw = tool_input.get(key)
            if not raw or not isinstance(raw, str):
                continue
            cand = Path(raw)
            target = (cand if cand.is_absolute() else root / cand).resolve()
            if target != root and root not in target.parents:
                _deny(f"cognit confines reads to {root}; refusing to access {target}.")
    except (ValueError, OSError) as e:  # e.g. embedded null byte → fail closed
        _deny(f"refusing malformed path argument: {e}")
    print(json.dumps({}))


if __name__ == "__main__":
    main()
