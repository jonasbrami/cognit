#!/usr/bin/env python
"""Materialize the pinned mermaid UMD bundle into the server's assets directory.

mermaid's browser build (~3.2 MB) is a *build artifact*, not source — so it is not
committed to git. This script is the single source of truth for fetching it, used at
two well-defined points:

  1. Packaging: ``hatch_build.py`` calls ``vendor()`` so the wheel/sdist ships the file.
  2. Dev/CI setup: run directly (``uv run python scripts/vendor_mermaid.py``).

Because the file ends up bundled and the local server serves it from ``/static/``, the
browser always loads mermaid from ``localhost`` — only the build/dev machine ever talks
to the CDN, and only once. The download is pinned and SHA-256 verified, so the bytes are
reproducible and tamper-evident. To upgrade mermaid, change VERSION and SHA256 together.
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.request
from pathlib import Path

VERSION = "10.9.6"
SHA256 = "eda3a0ad572bbe69a318c1be0163e8233dd824f3f12939e5168feba207767151"
URL = f"https://cdn.jsdelivr.net/npm/mermaid@{VERSION}/dist/mermaid.min.js"
DEST = Path(__file__).resolve().parents[1] / "src/cognit/server/assets/mermaid.min.js"
TIMEOUT_S = 60
RETRIES = 4  # tolerate transient CDN/DNS hiccups so a flaky network doesn't break CI
BACKOFF_S = 2


def vendor(dest: Path = DEST, *, force: bool = False) -> Path:
    """Download + verify the mermaid bundle into ``dest``. Idempotent unless ``force``.

    Returns the destination path. Raises ``RuntimeError`` if the download keeps failing
    or the bytes don't match the pinned hash (we refuse to write unverified content).
    """
    if dest.exists() and not force:
        return dest
    data = _fetch_verified()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so an interrupted download never leaves a corrupt asset behind.
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def _fetch_verified() -> bytes:
    last: OSError | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(URL, timeout=TIMEOUT_S) as resp:
                data = resp.read()
        except OSError as exc:  # DNS/connection/timeout/HTTP error — retry these
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(BACKOFF_S * (attempt + 1))
            continue
        # A hash mismatch is not transient: fail fast rather than retrying bad bytes.
        actual = hashlib.sha256(data).hexdigest()
        if actual != SHA256:
            raise RuntimeError(
                f"mermaid {VERSION} integrity check failed: expected {SHA256}, got {actual}"
            )
        return data
    raise RuntimeError(
        f"could not download mermaid {VERSION} from {URL} after {RETRIES} attempts: {last}"
    )


if __name__ == "__main__":
    try:
        out = vendor(force="--force" in sys.argv[1:])
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")
    print(f"mermaid {VERSION} -> {out}")
