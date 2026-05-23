import subprocess

# Files we never want in the prompt context: vendored/minified bundles, lockfiles,
# and binaries. They bloat the context (e.g. a 3.2 MB `mermaid.min.js`, or a one-line
# minified file whose diff is a single megabytes-wide `+` line) without helping the
# model understand the change. `fetch_pr_diff` drops their `diff --git` sections.
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


def _diff_git_target_path(header: str) -> str:
    """Extract the `b/<path>` path from a `diff --git a/<path> b/<path>` header line."""
    parts = header.split()
    target = parts[-1]
    return target[2:] if target.startswith("b/") else target


def _filter_diff(diff: str) -> str:
    """Drop the `diff --git` sections whose target path is denylisted.

    A unified diff from `gh pr diff` is a concatenation of per-file sections, each
    beginning with `diff --git a/<path> b/<path>`. We keep a section iff its target
    path is not vendored/minified/lock/binary (see `_skip_file_content`). Any preamble
    before the first `diff --git` is preserved.
    """
    sections: list[str] = []
    current: list[str] = []
    keep = True
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current and keep:
                sections.append("".join(current))
            current = [line]
            keep = not _skip_file_content(_diff_git_target_path(line))
        else:
            current.append(line)
    if current and keep:
        sections.append("".join(current))
    return "".join(sections)


def fetch_pr_diff(pr_url_or_number: str) -> str:
    """Return the PR's unified diff with vendored/minified/lock/binary sections stripped.

    The agentic outline path calls this both to gate on diff size (line count) and as
    the `pr_diff` tool the agent invokes — so stripping bloat here protects the model's
    context window from a single huge minified-file diff.
    """
    raw = subprocess.run(
        ["gh", "pr", "diff", pr_url_or_number],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return _filter_diff(raw)
