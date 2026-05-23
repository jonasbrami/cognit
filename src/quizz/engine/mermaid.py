"""Mermaid diagram validator.

Layered design — picks the best available validator at call time:

    1. Native `mmdc` (`@mermaid-js/mermaid-cli`) if on PATH.
    2. Dockerised parse-only validator if `docker` is on PATH. Built lazily from
       the `_mermaid_docker/` package on first use (~30s one-time).
    3. Python regex backstop — runs unconditionally as a first gate so even hosts
       without mmdc *or* docker get *some* validation.

The Python check catches the common LLM failure modes (no diagram header, grossly
unbalanced brackets, `[/text]` shape declarations where the LLM meant a rectangle
with a URL-like label). The native / dockerised layers are authoritative when
available.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

import typer

logger = logging.getLogger("quizz.engine.mermaid")


class MermaidUnavailable(RuntimeError):
    """Raised when strict=True and no real parser (mmdc OR docker) is available."""


# Recognized mermaid diagram-type headers. We only allow a subset matching what the
# artisan prompt is told to produce so an unfamiliar header reads as "wrong syntax"
# rather than "weird but valid".
_ALLOWED_HEADERS = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram)\b",
    re.MULTILINE,
)

# `[/foo]` / `[\foo]` / `[/foo/]` are *parallelogram* / *trapezoid* shape declarations
# in mermaid, NOT rectangles with a leading slash. LLMs trip on this every time they
# put a URL-like path ("/submit", "/publish") inside a rectangle node — the whole
# diagram fails to parse. Reject pre-emptively so generation retries instead.
#
# Trade-off: this regex ALSO rejects legitimate trapezoid/parallelogram syntax. That's
# consistent with the artisan prompt (rule 3 only allows rectangles, sequence, class,
# state shapes — no trapezoids). If we ever loosen the prompt to allow them, this
# regex needs to be reworked to distinguish "leading slash in a label" from "trapezoid
# shape declaration".
_LEADING_SLASH_IN_LABEL = re.compile(r"\[\s*[/\\]")

# Bracket-balance tolerance. Edge labels can include text with parentheses, so a small
# mismatch is normal; >4 indicates truncated or malformed output.
_BRACKET_IMBALANCE_TOLERANCE = 4

# Docker image we build on first use. Lives inside the package so it ships in the
# wheel; the user doesn't have to clone the repo to use the dockerised path.
_DOCKER_IMAGE_TAG = "quizz-mermaid-validator:local"
_DOCKER_BUILD_TIMEOUT_S = 300
_DOCKER_RUN_TIMEOUT_S = 30

# Cache of `docker image inspect` verdicts across calls within the same process.
# Without this, a quiz with N mermaid questions × 4 options × possible retries forks
# `docker image inspect` once per validation — wasteful, and slow if the Docker daemon
# is unresponsive. None means "not checked yet", True/False are sticky for the process.
_image_present_cache: bool | None = None


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _python_side_check(source: str) -> bool:
    """Lightweight syntax check that runs unconditionally.

    Catches the obvious failure modes without needing any external tool: empty
    source, missing diagram-type header, grossly unbalanced brackets, and labels
    that start with `/` or `\\` (which mermaid silently interprets as a shape
    declaration, not a label).
    """
    if not source or not source.strip():
        logger.debug("python check: empty source -> reject")
        return False
    if not _ALLOWED_HEADERS.search(source):
        logger.debug("python check: missing/unrecognised diagram header -> reject")
        return False
    # Bracket balance — mermaid uses [], (), {}. Tolerate a small mismatch because
    # edge labels can include text with parentheses.
    for opener, closer in (("[", "]"), ("(", ")"), ("{", "}")):
        if abs(source.count(opener) - source.count(closer)) > _BRACKET_IMBALANCE_TOLERANCE:
            logger.debug("python check: bracket imbalance for %s/%s -> reject", opener, closer)
            return False
    if _LEADING_SLASH_IN_LABEL.search(source):
        logger.debug("python check: leading `/` in label (parallelogram trap) -> reject")
        return False
    logger.debug("python check: passed")
    return True


def _native_mmdc_validate(mmdc: str, source: str) -> bool:
    """Run the locally-installed `mmdc` binary as the parse oracle.

    Returns False on parse failure OR on subprocess errors (timeout, OSError). A
    hung mmdc shouldn't crash the quiz-generation retry loop; treat it as "invalid"
    and let the upstream retry try again with fresh LLM output.
    """
    import tempfile

    logger.debug("validator: native mmdc at %s", mmdc)
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.mmd"
        out = Path(tmp) / "out.svg"
        inp.write_text(source)
        try:
            result = subprocess.run(
                [mmdc, "-i", str(inp), "-o", str(out)],
                capture_output=True,
                text=True,
                timeout=_DOCKER_RUN_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug("native mmdc failed to run (%s) — treating as invalid", e)
            return False
        ok = result.returncode == 0
        logger.debug("native mmdc verdict: %s", "valid" if ok else "INVALID")
        return ok


def _docker_image_dir() -> Path:
    """Directory containing the Dockerfile + validate.mjs. Ships inside the wheel."""
    return Path(__file__).parent / "_mermaid_docker"


def _docker_image_exists(tag: str) -> bool:
    """Cached `docker image inspect` check. None → check, then sticky for the process."""
    global _image_present_cache
    if _image_present_cache is not None:
        return _image_present_cache
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            timeout=_DOCKER_RUN_TIMEOUT_S,
        )
        _image_present_cache = result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("docker image inspect failed (%s) — treating as missing", e)
        _image_present_cache = False
    return _image_present_cache


def _docker_build_image(tag: str) -> bool:
    """Build the local validator image. Returns True on success, False otherwise."""
    global _image_present_cache
    image_dir = _docker_image_dir()
    if not (image_dir / "Dockerfile").exists():
        logger.debug("docker build: Dockerfile not found at %s", image_dir)
        return False
    typer.echo(
        f"Building mermaid validator image {tag} (one-time, ~30s)...",
        err=True,
    )
    logger.debug("docker build: starting (dir=%s, tag=%s)", image_dir, tag)
    try:
        result = subprocess.run(
            ["docker", "build", "-q", "-t", tag, str(image_dir)],
            capture_output=True,
            timeout=_DOCKER_BUILD_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("docker build failed to run (%s)", e)
        return False
    ok = result.returncode == 0
    logger.debug("docker build: %s", "ok" if ok else "FAILED")
    if ok:
        _image_present_cache = True
    return ok


def _docker_validate(source: str) -> bool | None:
    """Run the dockerised validator. Returns True/False, or None if Docker setup failed.

    None means "couldn't run the validator at all" — caller decides whether to fall
    back to the Python verdict or raise. A failed `docker run` (timeout, daemon
    unreachable, OSError) also returns None, not False, since "the validator broke"
    is materially different from "the diagram is invalid".
    """
    logger.debug("validator: dockerised (tag=%s)", _DOCKER_IMAGE_TAG)
    if not _docker_image_exists(_DOCKER_IMAGE_TAG):
        logger.debug("docker image %s not present — building", _DOCKER_IMAGE_TAG)
        if not _docker_build_image(_DOCKER_IMAGE_TAG):
            logger.debug("docker build failed — validator unavailable")
            return None
    else:
        logger.debug("docker image %s already present (cached)", _DOCKER_IMAGE_TAG)
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "-i", _DOCKER_IMAGE_TAG],
            input=source,
            text=True,
            capture_output=True,
            timeout=_DOCKER_RUN_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("docker run failed (%s) — validator unavailable", e)
        return None
    ok = result.returncode == 0
    logger.debug("docker validator verdict: %s", "valid" if ok else "INVALID")
    if not ok and result.stderr:
        logger.debug("docker validator stderr: %s", result.stderr.strip())
    return ok


def is_valid_mermaid(source: str, *, strict: bool = False) -> bool:
    """Parse-check a mermaid source through the best validator available.

    Order: Python regex → native `mmdc` → dockerised validator. The Python check
    is a fast gate; the other two are authoritative when present.

    Note: the Python regex is intentionally conservative — it rejects shapes the
    artisan prompt doesn't allow (e.g. trapezoids via `[/text\\]`). That means a
    diagram an authoritative parser would accept *can* be rejected here. This is
    fine while the artisan prompt forbids those shapes; if the prompt is ever
    loosened, this gate must be loosened in lockstep (see comment on
    `_LEADING_SLASH_IN_LABEL`).
    """
    if not _python_side_check(source):
        return False
    if (mmdc := _which("mmdc")) is not None:
        return _native_mmdc_validate(mmdc, source)
    if _which("docker") is not None:
        verdict = _docker_validate(source)
        if verdict is not None:
            return verdict
        logger.debug("docker layer unavailable, falling through")
    if strict:
        logger.debug("validator: strict=True and no mmdc/docker — raising")
        raise MermaidUnavailable(
            "no mermaid validator available — install `mmdc` "
            "(`npm install -g @mermaid-js/mermaid-cli`) or `docker`."
        )
    logger.debug("validator: python-only (no mmdc, no docker)")
    return True
