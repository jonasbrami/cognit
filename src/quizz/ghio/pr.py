import json
import subprocess
from dataclasses import dataclass


@dataclass
class PRInfo:
    number: int
    title: str
    body: str
    repo: str  # owner/name
    branch: str
    author: str


def fetch_pr_info(pr_url_or_number: str) -> PRInfo:
    """Fetch PR metadata via `gh pr view --json ...`."""
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            pr_url_or_number,
            "--json",
            "number,title,body,headRepository,headRefName,author",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return PRInfo(
        number=data["number"],
        title=data["title"],
        body=data["body"] or "",
        repo=data["headRepository"]["nameWithOwner"],
        branch=data["headRefName"],
        author=data["author"]["login"],
    )


def post_comment(pr_url_or_number: str, body: str) -> None:
    subprocess.run(
        ["gh", "pr", "comment", pr_url_or_number, "--body", body],
        check=True,
    )


def list_comments(pr_url_or_number: str) -> list[dict[str, object]]:
    result = subprocess.run(
        ["gh", "pr", "view", pr_url_or_number, "--json", "comments"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["comments"]  # type: ignore[no-any-return]


def find_latest_marker_comment(pr_url_or_number: str, marker: str) -> str | None:
    """Return the body of the most recent PR comment containing `marker`, or None."""
    comments = list_comments(pr_url_or_number)
    matching = [c for c in comments if marker in str(c["body"])]
    if not matching:
        return None
    matching.sort(key=lambda c: str(c["createdAt"]), reverse=True)
    return str(matching[0]["body"])
