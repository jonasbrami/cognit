import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class MermaidUnavailable(RuntimeError):
    """Raised when strict=True and mmdc is not installed."""


# Recognized mermaid diagram-type headers. We only allow a subset matching what the
# artisan prompt is told to produce (no journey/gantt/pie/etc) so that an unfamiliar
# header reads as "wrong syntax" rather than "weird but valid".
_ALLOWED_HEADERS = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram)\b",
    re.MULTILINE,
)


def _which_mmdc() -> str | None:
    return shutil.which("mmdc")


def _python_side_check(source: str) -> bool:
    """Lightweight syntax check that runs regardless of mmdc availability.

    Catches the obvious failure modes: empty source, missing diagram-type header,
    grossly unbalanced brackets. Not a substitute for mmdc, but ensures that users
    without mmdc installed still get *some* validation rather than silently
    accepting whatever the LLM produced.
    """
    if not source or not source.strip():
        return False
    if not _ALLOWED_HEADERS.search(source):
        return False
    # Bracket balance — mermaid uses [], (), {}. We allow a small mismatch tolerance
    # because edge labels can include text with parentheses, but a 5+ delta indicates
    # truncated or malformed output.
    for opener, closer in (("[", "]"), ("(", ")"), ("{", "}")):
        if abs(source.count(opener) - source.count(closer)) > 4:
            return False
    return True


def is_valid_mermaid(source: str, *, strict: bool = False) -> bool:
    """Parse-check a mermaid source.

    Always runs the Python-side lightweight check first. If mmdc is installed, also
    runs the real parser (authoritative). If mmdc is missing, returns the Python
    result unless strict=True (in which case the missing mmdc is itself an error).
    """
    if not _python_side_check(source):
        return False
    mmdc = _which_mmdc()
    if mmdc is None:
        if strict:
            raise MermaidUnavailable("mmdc not on PATH; install @mermaid-js/mermaid-cli")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.mmd"
        out = Path(tmp) / "out.svg"
        inp.write_text(source)
        result = subprocess.run(
            [mmdc, "-i", str(inp), "-o", str(out)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
