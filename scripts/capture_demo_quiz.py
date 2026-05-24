"""Capture a REAL cognit-generated quiz for the README demo.

Runs the actual generation engine (`ClaudeAgentLLM` → the `claude` binary) against
a live PR, recording both the generated quiz and the activity feed Claude emits
while it reads the diff. The demo recorder (`record_demo.py`) replays these so the
GIF shows genuine, model-authored questions — not a hand-written stand-in — while
staying offline and deterministic on re-record.

Usage:
    uv run python scripts/capture_demo_quiz.py <pr-url> [--model claude-sonnet-4-6]

Writes:
    scripts/demo_data/quiz.json   — the generated Quiz (post label-neutralization)
    scripts/demo_data/feed.json   — the activity events (thinking/text/tool_use)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cognit.engine.generate import generate_quiz
from cognit.engine.llm_claude_agent import ClaudeAgentLLM
from cognit.ghio.pr import fetch_pr_info

OUT_DIR = Path(__file__).resolve().parent / "demo_data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pr_url")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    args = ap.parse_args()

    info = fetch_pr_info(args.pr_url)
    print(f"PR #{info.number}: {info.title!r} (branch {info.branch})")

    events: list[dict[str, Any]] = []
    llm = ClaudeAgentLLM(model=args.model)
    llm.on_event = events.append

    print(f"generating with {args.model} (this can take a few minutes)…")
    quiz = generate_quiz(
        pr_title=info.title,
        pr_body=info.body,
        pr_number=info.number,
        pr_url=args.pr_url,
        branch=info.branch,
        llm=llm,
        model=args.model,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "quiz.json").write_text(quiz.model_dump_json(indent=2))
    (OUT_DIR / "feed.json").write_text(json.dumps(events, indent=2))

    kinds = {q.type: sum(1 for x in quiz.questions if x.type == q.type) for q in quiz.questions}
    print(f"wrote {OUT_DIR}/quiz.json — {len(quiz.questions)} questions {kinds}")
    print(f"wrote {OUT_DIR}/feed.json — {len(events)} activity events")


if __name__ == "__main__":
    main()
