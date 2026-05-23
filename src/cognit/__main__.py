"""Module entry point so `python -m cognit` runs the CLI.

Mirrors the `cognit` console script (`cognit.cli:app`) declared in pyproject.
"""

from cognit.cli import app

if __name__ == "__main__":
    app()
