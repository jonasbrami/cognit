# cognit host launcher (Milestone 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `cognit take` launch a confined `claude` session wired to the M1 MCP server that generates a quiz from a **real PR diff** and hands the developer an interactive, steerable session.

**Architecture:** `cognit take` becomes a thin launcher: detect the PR, preflight `claude`, pick a free port, write an MCP config + a confinement-hook settings file, set env (PR/port/snapshot/repo-root), and `exec` a confined `claude` (`--tools "Read Grep Glob"`, `--strict-mcp-config`, `--setting-sources user`, `--permission-mode bypassPermissions`, `--append-system-prompt <host system_generate.txt>`) with a kickoff prompt. The agent pulls the diff via new `file_diff`/`changed_files` MCP tools, reads the working tree (confined to the repo by a command `PreToolUse` hook), and calls `set_quiz`. The MCP server opens the browser when its web host starts. Builds on M1 (`cognit.mcp.*`).

**Tech Stack:** Same as M1 + `os.execvpe` for the TTY handoff; reuses `cognit.ghio.diff` (`fetch_pr_diff`/`split_diff`/`summarize_diff`), `cognit.ghio.pr.fetch_pr_info`, and the spike-validated `claude` flags.

**Scope note:** Retiring the OLD generation path (`draft_quiz`, `generate_quiz`, the broker serve-then-generate in `take.py`, the `/progress` generation feed) and updating its tests is deferred to the QA/cleanup phase after M3, so we don't block the new flow on test fallout. M3 (browser mermaid rendering, `replace_question` validation parity, lock-coherent grading) is a separate plan.

---

## File structure

- `src/cognit/mcp/server.py` (modify) — add `do_file_diff`, a `_DiffProvider` (lazy fetch+cache), and `file_diff`/`changed_files` tools; `_build_mcp` gains `pr_url`.
- `src/cognit/mcp/confine.py` (create) — the read-confinement `PreToolUse` command hook (stdin JSON → allow/deny), runnable as `python -m cognit.mcp.confine`.
- `src/cognit/mcp/launch.py` (create) — `build_launch_spec(...) -> LaunchSpec` (pure; argv + env + config file contents) so the launch is unit-testable; the impure `exec` stays in the CLI.
- `src/cognit/engine/prompts/system_generate.txt` (modify) — host-adapt (render via `set_quiz`, revise via `replace_question`, pull diff via `changed_files`/`file_diff`); keep the quality + progression guidance.
- `src/cognit/cli/take.py` (modify) — `run()` becomes the launcher (build spec → write temp configs → `os.execvpe`).
- `src/cognit/mcp/server.py` `main()` (modify) — open the browser when the web host is up.
- Tests: `tests/mcp/test_file_diff.py`, `tests/mcp/test_confine.py`, `tests/mcp/test_launch.py`, plus a `tests/mcp/test_generation_e2e.py` headless QA (gated on `claude` availability).

---

## Task 1: `file_diff` + `changed_files` MCP tools

**Files:** Modify `src/cognit/mcp/server.py`; Test `tests/mcp/test_file_diff.py`.

- [ ] **Step 1: failing tests.** `tests/mcp/test_file_diff.py`:
```python
from cognit.mcp.server import do_file_diff

SECTIONS = {
    "src/a.py": "diff --git a/src/a.py b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n",
    "src/b.py": "diff --git a/src/b.py b/src/b.py\n@@ -1 +1 @@\n-p\n+q\n",
}


def test_exact_path():
    assert "x\n+y" in do_file_diff("src/a.py", SECTIONS)


def test_basename_fallback():
    assert "p\n+q" in do_file_diff("b.py", SECTIONS)


def test_unknown_path_lists_changed_files():
    out = do_file_diff("nope.py", SECTIONS)
    assert "No changed file matches" in out and "src/a.py" in out and "src/b.py" in out


def test_ambiguous_basename_not_guessed():
    sections = {"x/a.py": "AAA", "y/a.py": "BBB"}
    out = do_file_diff("a.py", sections)
    assert "No changed file matches" in out  # two matches → refuse to guess
```

- [ ] **Step 2: run — expect FAIL** (`ImportError: do_file_diff`): `uv run pytest tests/mcp/test_file_diff.py -v`

- [ ] **Step 3: implement.** In `src/cognit/mcp/server.py`, add imports `from cognit.ghio.diff import fetch_pr_diff, split_diff, summarize_diff` and:
```python
def do_file_diff(path: str, sections: dict[str, str]) -> str:
    """Return the diff section for ONE changed file, tolerating basename/repo-relative
    variants. Refuses to guess when a basename matches more than one file."""
    section = sections.get(path)
    if section is None:
        hits = [p for p in sections if p.endswith(path) or p.rsplit("/", 1)[-1] == path]
        section = sections[hits[0]] if len(hits) == 1 else None
    if section is None:
        listing = ", ".join(sorted(sections)) or "(none)"
        return f"No changed file matches {path!r}. Changed files: {listing}"
    return section


class _DiffProvider:
    """Lazily fetch + cache the PR's filtered diff once per process."""

    def __init__(self, pr_url: str) -> None:
        self._pr_url = pr_url
        self._sections: dict[str, str] | None = None
        self._raw: str | None = None

    def _ensure(self) -> None:
        if self._raw is None:
            self._raw = fetch_pr_diff(self._pr_url)
            self._sections = split_diff(self._raw)

    def sections(self) -> dict[str, str]:
        self._ensure()
        assert self._sections is not None
        return self._sections

    def overview(self) -> str:
        self._ensure()
        assert self._raw is not None
        return summarize_diff(self._raw)
```
Then in `_build_mcp(state, llm, pr_url)` (add the `pr_url` param), create `diffs = _DiffProvider(pr_url)` and register two more sync tools:
```python
    @mcp.tool()
    def changed_files() -> str:
        """List the PR's changed files with +/- line counts. Call this first, then
        file_diff(path) for the files worth quizzing."""
        return diffs.overview()

    @mcp.tool()
    def file_diff(path: str) -> str:
        """Fetch the diff hunks for ONE changed file (a path from changed_files)."""
        return do_file_diff(path, diffs.sections())
```
Update the `main()` call site to `_build_mcp(state, llm, pr_url)`.

- [ ] **Step 4: run — expect PASS** (4 tests): `uv run pytest tests/mcp/test_file_diff.py -v`
- [ ] **Step 5: verify** `uv run mypy` (0 errors) + `uv run ruff check src/cognit/mcp/server.py tests/mcp/test_file_diff.py`.
- [ ] **Step 6: commit.**
```bash
git add src/cognit/mcp/server.py tests/mcp/test_file_diff.py
git commit -m "feat(mcp): file_diff + changed_files tools (agent pulls PR hunks)" \
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: read-confinement command hook (`confine.py`)

**Files:** Create `src/cognit/mcp/confine.py`; Test `tests/mcp/test_confine.py`. (Productionizes the spike's `confine_read.py`; the repo root comes from `COGNIT_REPO_ROOT` env so it's not hard-coded.)

- [ ] **Step 1: failing tests.** `tests/mcp/test_confine.py`:
```python
import json
import subprocess
import sys
from pathlib import Path


def _run(tool_input: dict, root: Path) -> dict:
    p = subprocess.run(
        [sys.executable, "-m", "cognit.mcp.confine"],
        input=json.dumps({"tool_name": "Read", "tool_input": tool_input}),
        capture_output=True, text=True, env={"COGNIT_REPO_ROOT": str(root), "PATH": ""},
    )
    return json.loads(p.stdout or "{}")


def test_in_repo_allowed(tmp_path: Path):
    assert _run({"file_path": str(tmp_path / "a.py")}, tmp_path) == {}


def test_out_of_repo_denied(tmp_path: Path):
    out = _run({"file_path": "/etc/passwd"}, tmp_path)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_dotdot_escape_denied(tmp_path: Path):
    out = _run({"file_path": "../../etc/shadow"}, tmp_path)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_glob_pattern_escape_denied(tmp_path: Path):
    out = _run({"pattern": "../../**/*.key"}, tmp_path)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
```

- [ ] **Step 2: run — expect FAIL** (`No module named cognit.mcp.confine`): `uv run pytest tests/mcp/test_confine.py -v`

- [ ] **Step 3: implement.** `src/cognit/mcp/confine.py`:
```python
"""Claude Code PreToolUse command hook: confine Read/Grep/Glob to the repo root.

Reads the PreToolUse JSON on stdin; denies any path-bearing argument that resolves
outside `COGNIT_REPO_ROOT`. Runnable as `python -m cognit.mcp.confine`. Mirrors the
SDK read-confinement hook, but as the external command form regular Claude Code uses.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Every path-bearing argument across Read/Grep/Glob — including Grep/Glob patterns,
# which can carry `../` escapes.
_PATH_KEYS = ("file_path", "path", "notebook_path", "pattern", "glob")


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    sys.exit(0)


def main() -> None:
    root = Path(os.environ.get("COGNIT_REPO_ROOT", ".")).resolve()
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    tool_input = data.get("tool_input") or {}
    for key in _PATH_KEYS:
        raw = tool_input.get(key)
        if not raw or not isinstance(raw, str):
            continue
        cand = Path(raw)
        target = (cand if cand.is_absolute() else root / cand).resolve()
        if target != root and root not in target.parents:
            _deny(f"cognit confines reads to {root}; refusing to access {target}.")
    print(json.dumps({}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: run — expect PASS** (4 tests): `uv run pytest tests/mcp/test_confine.py -v`
- [ ] **Step 5: verify** `uv run mypy` + `uv run ruff check src/cognit/mcp/confine.py tests/mcp/test_confine.py`.
- [ ] **Step 6: commit.**
```bash
git add src/cognit/mcp/confine.py tests/mcp/test_confine.py
git commit -m "feat(mcp): read-confinement PreToolUse command hook" \
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: host-adapt the generation system prompt

**Files:** Modify `src/cognit/engine/prompts/system_generate.txt`; Modify `tests/engine/test_prompts.py`.

The current prompt tells the agent to "Submit the complete quiz … via the `submit_quiz` tool" and references the SDK one-shot flow. Host-adapt it WITHOUT losing the quality guidance (lookup test, question types, mermaid rules, the usefulness check, AND the progression/meaningful guidance added in `24a95d2`).

- [ ] **Step 1: edit the prompt.** Replace the input-handling + output paragraphs so they describe the host tools. Specifically:
  - In "## Input handling": replace the `file_diff(path)`/SDK framing with: "Call `changed_files` to see the PR's changed files, then `file_diff(path)` to pull the hunks for the ones worth quizzing. Use `Read`/`Grep`/`Glob` for surrounding unchanged context (confined to the repo)." Keep the prompt-injection-resistance sentence verbatim.
  - Replace the "## Output" section: "Render the quiz with the `set_quiz` tool (`{version, questions:[…]}`, mermaid fully rendered). If it's rejected, you'll get per-issue reasons — fix and call `set_quiz` again. When the reader asks you to change a question, use `replace_question(index, question)`. When they say they're ready, call `grade`. Do not narrate the quiz in text — always drive it through the tools."
  - Keep ALL of: the Frame (incl. the progression + calibrate bullets), question types, mermaid drawing rules, and the usefulness check.

- [ ] **Step 2: update `tests/engine/test_prompts.py`.** The `test_system_generate_has_quality_anchors` test asserts `"lookup test"`, `"usefulness check"`, `"explanation"`, `"progression"` — all still present, keep. ADD `assert "set_quiz" in sys_prompt` and remove any assertion that requires `submit_quiz` if present. Confirm `generate.txt` (the old user-prompt template) is NOT referenced by the host flow — if `test_generate_txt_formats_with_all_placeholders` still passes (the file is unchanged), leave it; it'll be removed in the cleanup phase.

- [ ] **Step 3: run** `uv run pytest tests/engine/test_prompts.py -v` → pass.
- [ ] **Step 4: commit.**
```bash
git add src/cognit/engine/prompts/system_generate.txt tests/engine/test_prompts.py
git commit -m "feat(prompts): host-adapt generation system prompt (set_quiz/file_diff)" \
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: browser auto-open when the web host starts

**Files:** Modify `src/cognit/mcp/server.py`.

- [ ] **Step 1: implement.** In `_start_web`, after building the app and before/at server start, open the browser once (best-effort, daemon). Add `import webbrowser` and `import threading` (already present). Change `_start_web` to schedule a browser open after a short readiness delay:
```python
def _start_web(state: QuizState, *, pr_url: str, port: int) -> None:
    app = build_web_app(state, post_comment=lambda body: gh_post_comment(pr_url, body))
    url = f"http://127.0.0.1:{port}"

    def _open() -> None:
        import time
        time.sleep(1.0)  # give uvicorn a moment to bind
        try:
            webbrowser.open(url)
        except Exception:  # headless / no browser — non-fatal
            logger.debug("could not open browser at %s", url)

    threading.Thread(target=_open, daemon=True).start()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    try:
        uvicorn.Server(cfg).run()
    except Exception:
        logger.exception("cognit web host on port %d exited unexpectedly", port)
        raise
```
Also have `main()` print the URL to stderr so a headless user sees it: after computing `port`, `print(f"cognit quiz: http://127.0.0.1:{port}", file=sys.stderr)` (add `import sys`).

- [ ] **Step 2: verify** `uv run pytest tests/mcp/ -q` (no regressions — the web tests don't exercise `_start_web`), `uv run mypy`, `uv run ruff check src/cognit/mcp/server.py`.
- [ ] **Step 3: commit.**
```bash
git add src/cognit/mcp/server.py
git commit -m "feat(mcp): open the browser + print the quiz URL when the host starts" \
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: the `cognit take` launcher

**Files:** Create `src/cognit/mcp/launch.py`; Modify `src/cognit/cli/take.py`; Test `tests/mcp/test_launch.py`.

The pure `build_launch_spec` is unit-tested; the CLI does the impure temp-file writes + `os.execvpe`.

- [ ] **Step 1: failing tests.** `tests/mcp/test_launch.py`:
```python
from pathlib import Path

from cognit.mcp.launch import build_launch_spec


def test_launch_spec_has_confined_flags(tmp_path: Path):
    spec = build_launch_spec(
        pr_url="https://github.com/o/r/pull/5", pr_number=5, branch="feat/x",
        port=8123, snapshot_path=tmp_path / "s.json", repo_root=tmp_path,
        mcp_config_path=tmp_path / "mcp.json", settings_path=tmp_path / "settings.json",
        system_prompt="SYS", model="claude-sonnet-4-6",
    )
    argv = spec.argv
    assert argv[0] == "claude"
    assert "--tools" in argv and "Read Grep Glob" in argv
    assert "--strict-mcp-config" in argv
    assert "--permission-mode" in argv and "bypassPermissions" in argv
    assert "--setting-sources" in argv and "user" in argv
    assert "--append-system-prompt" in argv and "SYS" in argv
    assert any("PR #5" in a for a in argv)  # kickoff mentions the PR
    # env carries the server's wiring
    assert spec.env["COGNIT_PR_URL"] == "https://github.com/o/r/pull/5"
    assert spec.env["COGNIT_PR_NUMBER"] == "5"
    assert spec.env["COGNIT_HTTP_PORT"] == "8123"
    assert spec.env["COGNIT_SNAPSHOT_PATH"] == str(tmp_path / "s.json")
    assert spec.env["COGNIT_REPO_ROOT"] == str(tmp_path)


def test_mcp_config_points_at_cognit_module(tmp_path: Path):
    spec = build_launch_spec(
        pr_url="u", pr_number=1, branch="b", port=1, snapshot_path=tmp_path / "s",
        repo_root=tmp_path, mcp_config_path=tmp_path / "m.json",
        settings_path=tmp_path / "set.json", system_prompt="S", model="m",
    )
    assert "cognit.mcp" in spec.mcp_config_json
    assert "PreToolUse" in spec.settings_json and "cognit.mcp.confine" in spec.settings_json
```

- [ ] **Step 2: run — expect FAIL**: `uv run pytest tests/mcp/test_launch.py -v`

- [ ] **Step 3: implement `src/cognit/mcp/launch.py`:**
```python
"""Pure construction of the confined `claude` launch (argv + env + config file contents).

Keeping this pure makes the launch unit-testable; the CLI writes the config files and
calls os.execvpe. The session is confined exactly like the SDK generation agent:
read-only built-in tools, strict MCP, branch settings ignored, plus a read-confinement
PreToolUse hook."""

from __future__ import annotations

import json
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


def build_launch_spec(
    *, pr_url: str, pr_number: int, branch: str, port: int, snapshot_path: Path,
    repo_root: Path, mcp_config_path: Path, settings_path: Path,
    system_prompt: str, model: str,
) -> LaunchSpec:
    py = sys.executable
    mcp_config = {"mcpServers": {"cognit": {"command": py, "args": ["-m", "cognit.mcp"]}}}
    settings = {"hooks": {"PreToolUse": [{
        "matcher": "Read|Grep|Glob",
        "hooks": [{"type": "command", "command": f"{py} -m cognit.mcp.confine"}],
    }]}}
    env = {
        "COGNIT_PR_URL": pr_url, "COGNIT_PR_NUMBER": str(pr_number),
        "COGNIT_HTTP_PORT": str(port), "COGNIT_SNAPSHOT_PATH": str(snapshot_path),
        "COGNIT_REPO_ROOT": str(repo_root),
    }
    argv = [
        "claude",
        "--model", model,
        "--tools", "Read Grep Glob",
        "--strict-mcp-config",
        "--mcp-config", str(mcp_config_path),
        "--settings", str(settings_path),
        "--setting-sources", "user",
        "--permission-mode", "bypassPermissions",
        "--append-system-prompt", system_prompt,
        _kickoff(pr_number, branch),
    ]
    return LaunchSpec(
        argv=argv, env=env,
        mcp_config_json=json.dumps(mcp_config), settings_json=json.dumps(settings),
    )
```

- [ ] **Step 4: rewrite `run()` in `src/cognit/cli/take.py`** to use it. Replace the body of `run()` (keep `_configure_logging`, `_detect_pr_from_branch`, `_free_port`, `_cache_path_for` helpers; the OLD serve-then-generate helpers are removed in the cleanup phase). New `run`:
```python
def run(pr: str | None, show_results: bool, model: str = "claude-sonnet-4-6") -> None:
    _configure_logging()
    pr_url = pr or _detect_pr_from_branch()
    if pr_url is None:
        typer.echo("error: no PR detected from current branch; pass --pr <url>")
        raise typer.Exit(code=1)
    # Preflight: the confined claude session is the whole app now.
    if shutil.which("claude") is None:
        typer.echo("error: `claude` not found. Install Claude Code and run `claude login`.")
        raise typer.Exit(code=1)
    info = fetch_pr_info(pr_url)
    if "quiz: skip" in info.body.lower():
        typer.echo("quiz: skip in PR body — skipping.")
        return
    repo_root = Path(
        subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                       text=True, check=True).stdout.strip()
    )
    port = _free_port()
    snapshot = _cache_path_for(pr_url)
    tmp = Path(tempfile.mkdtemp(prefix="cognit-"))
    mcp_cfg, settings = tmp / "mcp.json", tmp / "settings.json"
    spec = build_launch_spec(
        pr_url=pr_url, pr_number=info.number, branch=info.branch, port=port,
        snapshot_path=snapshot, repo_root=repo_root, mcp_config_path=mcp_cfg,
        settings_path=settings, system_prompt=_load_host_prompt(), model=model,
    )
    mcp_cfg.write_text(spec.mcp_config_json)
    settings.write_text(spec.settings_json)
    typer.echo(f"cognit: launching quiz session for PR #{info.number} (browser opens shortly)…")
    os.execvpe("claude", spec.argv, {**os.environ, **spec.env})
```
Add the needed imports to `take.py` (`os`, `shutil`, `tempfile`, `Path`, `build_launch_spec`, `fetch_pr_info`) and a `_load_host_prompt()` that returns the host system prompt:
```python
from importlib import resources
def _load_host_prompt() -> str:
    return resources.files("cognit.engine.prompts").joinpath("system_generate.txt").read_text()
```

- [ ] **Step 5: run** `uv run pytest tests/mcp/test_launch.py -v` → pass. Then `uv run mypy` + `uv run ruff check src/cognit/mcp/launch.py src/cognit/cli/take.py`. (Existing `take.py` tests may break because the old flow is gone — if `tests/cli/test_take*.py` fail, note them as expected casualties for the cleanup phase; do NOT delete them in this task, just report. If they import removed symbols, this task should keep those symbols until cleanup — so DO NOT delete the old helpers yet, only repoint `run()`.)
- [ ] **Step 6: commit.**
```bash
git add src/cognit/mcp/launch.py src/cognit/cli/take.py tests/mcp/test_launch.py
git commit -m "feat(cli): cognit take launches a confined claude session (host model)" \
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: headless end-to-end generation QA

**Files:** Create `tests/mcp/test_generation_e2e.py` (skipped if `claude` is unavailable).

Proves the agent, driven by the host prompt + MCP tools, actually generates a quiz from a real diff — without an interactive TTY (uses `claude -p` against the real MCP server config on the **current branch's own diff vs main**).

- [ ] **Step 1: write the test.** `tests/mcp/test_generation_e2e.py`:
```python
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="claude not installed")


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
           "COGNIT_HTTP_PORT": "8765", "COGNIT_SNAPSHOT_PATH": str(snapshot),
           "COGNIT_REPO_ROOT": repo}
    # The agent can't fetch a PR diff here (DUMMY url); feed it via the prompt and tell it
    # to skip changed_files/file_diff. This still exercises set_quiz end to end.
    diff = subprocess.run(["git", "diff", "main...HEAD", "--", "src/cognit/mcp/state.py"],
                          capture_output=True, text=True, cwd=repo).stdout[:6000]
    kickoff = ("Build a 2-question quiz about this diff and render it with set_quiz; do NOT "
               f"call changed_files/file_diff. Then stop.\n\n<diff>\n{diff}\n</diff>")
    subprocess.run(
        ["claude", "-p", kickoff, "--mcp-config", str(mcp_cfg), "--strict-mcp-config",
         "--append-system-prompt", sys_prompt, "--permission-mode", "bypassPermissions",
         "--tools", "Read Grep Glob", "--model", "sonnet"],
        env=env, capture_output=True, text=True, timeout=180, cwd=repo,
    )
    data = json.loads(snapshot.read_text())
    assert data["quiz"] is not None
    assert len(data["quiz"]["questions"]) >= 1
```

- [ ] **Step 2: run** `uv run pytest tests/mcp/test_generation_e2e.py -v` → PASS (or SKIP if no `claude`). If it FAILS because the agent narrated instead of calling `set_quiz`, that's signal the host prompt needs tightening — fix the prompt (Task 3) and re-run.
- [ ] **Step 3: commit.**
```bash
git add tests/mcp/test_generation_e2e.py
git commit -m "test(mcp): headless end-to-end generation QA (claude -p drives set_quiz)" \
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage (design §4–§5, M2):** launcher exec's confined `claude` (Task 5) ✅; `file_diff`/`changed_files` so the agent pulls the diff (Task 1) ✅; read-confinement hook shipped + wired via `--settings` (Tasks 2, 5) ✅; host-adapted prompt (Task 3) ✅; browser auto-open + URL (Task 4) ✅; PR detection + `quiz: skip` opt-out + `claude` preflight (Task 5) ✅; env plumbing (Task 5, validated in the spike) ✅; headless generation QA (Task 6) ✅.

**Deferred (recorded, not gaps):** retiring the OLD generation path + its tests → QA/cleanup phase; mermaid rendering in the browser, `replace_question` validation parity, lock-coherent grading → M3.

**Placeholder scan:** none. **Type consistency:** `build_launch_spec` kwargs match the test + the CLI call site; `_build_mcp` gains `pr_url` consistently (Task 1 step 3 updates the `main()` call site); `do_file_diff(path, sections)` matches its tests.
