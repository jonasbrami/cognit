"""`python -m cognit` should invoke the same CLI as the `cognit` console script."""

import subprocess
import sys


def test_python_m_cognit_runs_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "cognit", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # The single `take` command should be listed.
    assert "take" in result.stdout


def test_python_m_cognit_no_args_shows_help_and_exits_nonzero():
    # `app` is built with no_args_is_help=True, so bare invocation prints help
    # and exits with a non-zero code (typer's missing-command convention).
    result = subprocess.run(
        [sys.executable, "-m", "cognit"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Usage" in result.stdout or "Usage" in result.stderr
