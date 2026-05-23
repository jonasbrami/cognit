import json
import subprocess
from typing import Callable


def fetch_diff_and_files(
    pr_url_or_number: str,
    *,
    fetch_file_contents: Callable[[str], str],
) -> tuple[str, dict[str, str]]:
    """Return (unified diff, {path: full_content_at_head})."""
    diff = subprocess.run(
        ["gh", "pr", "diff", pr_url_or_number],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    files_json = subprocess.run(
        ["gh", "pr", "view", pr_url_or_number, "--json", "files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = [f["path"] for f in json.loads(files_json)["files"]]

    contents: dict[str, str] = {}
    for p in paths:
        try:
            contents[p] = fetch_file_contents(p)
        except Exception:
            contents[p] = ""
    return diff, contents


def read_file_at_head(path: str) -> str:
    """Default file fetcher: read from local checkout (used in CI after `actions/checkout`)."""
    return subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
