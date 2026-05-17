import shutil
import subprocess
import tempfile
from pathlib import Path


class MermaidUnavailable(RuntimeError):
    """Raised when strict=True and mmdc is not installed."""


def _which_mmdc() -> str | None:
    return shutil.which("mmdc")


def is_valid_mermaid(source: str, *, strict: bool = False) -> bool:
    """Parse-check a mermaid source. If mmdc is missing, skip (return True) unless strict."""
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
