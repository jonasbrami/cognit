"""Manual smoke test for the engine.

Usage:
    GITHUB_TOKEN=<pat-with-models-scope> uv run python scripts/dev_generate.py <diff-file>

Reads a unified diff from the given path, generates a quiz against GitHub Models,
and prints the resulting Quiz JSON to stdout. No PR is fetched; the diff is the
entire context.
"""

import sys
from pathlib import Path

from quizz.engine.generate import generate_quiz
from quizz.engine.llm_githubmodels import GitHubModelsLLM


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: dev_generate.py <diff-file>")
    diff = Path(sys.argv[1]).read_text()
    quiz = generate_quiz(
        diff=diff,
        pr_title="dev test",
        pr_body="",
        files={},
        pr_number=0,
        llm=GitHubModelsLLM(),
    )
    print(quiz.model_dump_json(indent=2))
