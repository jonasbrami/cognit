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
    """Extract the target (`b/`) path from a `diff --git a/<old> b/<new>` header line.

    Paths may contain spaces (git does not quote them) and are identical when the
    change is not a rename, so a naive whitespace split truncates names like
    `a/my file.js b/my file.js`. Split on the last ` b/` separator instead, and
    strip git's surrounding quotes for non-ASCII names. Falls back to the trailing
    path component for unexpected headers.
    """
    body = header[len("diff --git ") :] if header.startswith("diff --git ") else header
    target = body.rsplit(" b/", 1)[1] if " b/" in body else body.rsplit("/", 1)[-1]
    return target.strip().strip('"')


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


def split_diff(diff: str) -> dict[str, str]:
    """Split a (filtered) unified diff into ``{target_path: section_text}``.

    Each `diff --git a/… b/…` section is keyed by its target path. Any preamble
    before the first section is dropped. Lets the agent pull one file's hunks at a
    time instead of receiving the whole diff up front.
    """
    sections: dict[str, str] = {}
    path: str | None = None
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if path is not None:
                sections[path] = "".join(current)
            path = _diff_git_target_path(line)
            current = [line]
        elif path is not None:
            current.append(line)
    if path is not None:
        sections[path] = "".join(current)
    return sections


def summarize_diff(diff: str) -> str:
    """A `--stat`-style overview: one line per changed file with +/- counts.

    Cheap and bounded — the agent triages from this, then pulls the files worth
    quizzing via the per-file diff tool, instead of loading the whole diff at once.
    """
    out: list[str] = []
    for path, section in split_diff(diff).items():
        adds = sum(
            1 for ln in section.splitlines() if ln.startswith("+") and not ln.startswith("+++")
        )
        dels = sum(
            1 for ln in section.splitlines() if ln.startswith("-") and not ln.startswith("---")
        )
        out.append(f"{path} | +{adds} -{dels}")
    return "\n".join(out) if out else "(no changed files after filtering)"


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
