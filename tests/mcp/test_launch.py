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
    assert any("PR #5" in a for a in argv)
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


def test_generate_kickoff_by_default(tmp_path: Path):
    spec = build_launch_spec(
        pr_url="u", pr_number=9, branch="b", port=1, snapshot_path=tmp_path / "s",
        repo_root=tmp_path, mcp_config_path=tmp_path / "m", settings_path=tmp_path / "set",
        system_prompt="S", model="m",
    )
    assert any("Generate a comprehension quiz" in a for a in spec.argv)


def test_resume_kickoff_when_resume_true(tmp_path: Path):
    spec = build_launch_spec(
        pr_url="u", pr_number=9, branch="b", port=1, snapshot_path=tmp_path / "s",
        repo_root=tmp_path, mcp_config_path=tmp_path / "m", settings_path=tmp_path / "set",
        system_prompt="S", model="m", resume=True,
    )
    assert any("already exists" in a and "Do NOT regenerate" in a for a in spec.argv)
    assert not any("Generate a comprehension quiz" in a for a in spec.argv)
