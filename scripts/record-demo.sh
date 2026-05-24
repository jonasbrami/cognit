#!/usr/bin/env bash
# Record the README demo GIF (docs/img/cognit-demo.gif).
#
# Prereqs (one-time): uv run playwright install chromium  (and ffmpeg on PATH)
# Usage: scripts/record-demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/record_demo.py
