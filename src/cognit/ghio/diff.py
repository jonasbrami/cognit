import json
import subprocess
from typing import Callable

# Files whose full HEAD contents we never inline into the prompt: vendored/minified
# bundles, lockfiles, and binaries. They bloat the context (e.g. a 3.2 MB
# `mermaid.min.js`) without helping the model understand the change — the diff
# already lists them as touched.
_SKIP_CONTENT_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
}
_SKIP_CONTENT_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".lock",  # uv.lock, yarn.lock, poetry.lock, Cargo.lock, ...
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".pdf",
    ".woff",
    ".woff2",
)


def _skip_file_content(path: str) -> bool:
    """True if `path` is a vendored/minified/lock/binary file we shouldn't inline."""
    name = path.rsplit("/", 1)[-1]
    return name in _SKIP_CONTENT_NAMES or path.endswith(_SKIP_CONTENT_SUFFIXES)


def fetch_diff_and_files(
    pr_url_or_number: str,
    *,
    fetch_file_contents: Callable[[str], str],
) -> tuple[str, dict[str, str]]:
    """Return (unified diff, {path: full_content_at_head}).

    Vendored/minified/lockfile/binary paths are listed in the diff but their full
    contents are omitted from the returned dict — see `_skip_file_content`.
    """
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
        if _skip_file_content(p):
            continue
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
