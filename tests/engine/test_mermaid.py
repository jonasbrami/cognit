import shutil

import pytest

from cognit.engine import mermaid as mermaid_mod
from cognit.engine.mermaid import MermaidUnavailable, is_valid_mermaid


@pytest.fixture(autouse=True)
def _reset_image_cache() -> None:
    """The docker image-presence cache is sticky across calls in real use; reset
    it before each test so tests don't bleed state into each other."""
    mermaid_mod._image_present_cache = None


# ---------- Native mmdc tests (skip if not installed) ----------


@pytest.mark.skipif(not shutil.which("mmdc"), reason="mmdc not installed")
def test_valid_diagram() -> None:
    assert is_valid_mermaid("flowchart LR\nA --> B")


@pytest.mark.skipif(not shutil.which("mmdc"), reason="mmdc not installed")
def test_invalid_diagram() -> None:
    assert not is_valid_mermaid("not actually mermaid {{{")


# ---------- Python-side check ----------


def _no_external_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend mmdc and docker are both missing so the Python check is the only gate."""
    monkeypatch.setattr("cognit.engine.mermaid._which", lambda cmd: None)


def test_python_side_check_accepts_valid_when_no_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_external_validators(monkeypatch)
    assert is_valid_mermaid("flowchart LR\n  A --> B", strict=False) is True


def test_python_side_check_rejects_obvious_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_external_validators(monkeypatch)
    assert is_valid_mermaid("anything", strict=False) is False
    assert is_valid_mermaid("", strict=False) is False
    assert is_valid_mermaid("flowchart LR\n[[[[[[", strict=False) is False


def test_python_side_check_rejects_leading_slash_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """`[/submit]` is mermaid's parallelogram-shape syntax, not a rectangle with a
    leading slash. LLMs hit this when labels contain URL paths. The Python check
    must reject it pre-emptively so generation retries instead of serving a broken
    diagram to the browser."""
    _no_external_validators(monkeypatch)
    diag = "flowchart LR\nA[/submit: Return Results] --> B[/publish: Render Comment]\n"
    assert is_valid_mermaid(diag, strict=False) is False


def test_python_side_check_accepts_quoted_path_in_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix for the leading-slash bug: wrap the URL-like path in double quotes."""
    _no_external_validators(monkeypatch)
    diag = 'flowchart LR\nA["/submit endpoint"] --> B["/publish endpoint"]\n'
    assert is_valid_mermaid(diag, strict=False) is True


# ---------- Strict mode ----------


def test_strict_raises_when_no_validator_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """strict=True without mmdc OR docker is a misconfiguration — raise loudly."""
    _no_external_validators(monkeypatch)
    with pytest.raises(MermaidUnavailable):
        is_valid_mermaid("flowchart LR\nA --> B", strict=True)


# ---------- Docker layer ----------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_docker_layer_invoked_when_only_docker_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `mmdc` is missing but `docker` is on PATH, validation must go through
    the dockerised validator — not silently fall back to the Python verdict."""

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/docker" if cmd == "docker" else None

    monkeypatch.setattr("cognit.engine.mermaid._which", fake_which)

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> _FakeCompleted:
        calls.append(args)
        # First call: `docker image inspect <tag>` → say "image exists" so we skip build.
        if "image" in args:
            return _FakeCompleted(0)
        # Second call: `docker run --rm -i <tag>` → say "parse succeeded".
        if "run" in args:
            return _FakeCompleted(0)
        return _FakeCompleted(1)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert is_valid_mermaid("flowchart LR\nA --> B", strict=False) is True
    # We expect at least an `image inspect` then a `run` — proving docker was invoked.
    inspect_seen = any("inspect" in args for args in calls)
    run_seen = any("run" in args for args in calls)
    assert inspect_seen, f"docker image inspect was not invoked; calls were {calls!r}"
    assert run_seen, f"docker run was not invoked; calls were {calls!r}"


def test_docker_layer_propagates_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the dockerised validator reports a parse error (exit 1), is_valid_mermaid
    returns False — without falling back to the optimistic Python verdict."""

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/docker" if cmd == "docker" else None

    monkeypatch.setattr("cognit.engine.mermaid._which", fake_which)

    def fake_run(args: list[str], **kwargs: object) -> _FakeCompleted:
        if "image" in args:
            return _FakeCompleted(0)  # image exists
        if "run" in args:
            return _FakeCompleted(1, stderr=b"Parse error on line 2")
        return _FakeCompleted(0)

    monkeypatch.setattr("subprocess.run", fake_run)

    # Passes the Python check (header + balanced brackets) but the dockerised
    # validator is authoritative and says no.
    assert is_valid_mermaid("flowchart LR\nA --> B", strict=False) is False


def test_docker_layer_handles_subprocess_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung docker daemon (TimeoutExpired) must NOT crash quiz generation. The
    validator returns None (= "unavailable"), the upstream caller decides."""
    import subprocess as _subprocess

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/docker" if cmd == "docker" else None

    monkeypatch.setattr("cognit.engine.mermaid._which", fake_which)

    def fake_run(args: list[str], **kwargs: object) -> _FakeCompleted:
        raise _subprocess.TimeoutExpired(cmd=args, timeout=30)

    monkeypatch.setattr("subprocess.run", fake_run)

    # No mmdc, docker hangs → falls through to Python verdict (which passes for this src).
    assert is_valid_mermaid("flowchart LR\nA --> B", strict=False) is True


def test_docker_image_inspect_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker image inspect` should fire at most once per process — successive
    validations reuse the cached "image present" verdict."""

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/docker" if cmd == "docker" else None

    monkeypatch.setattr("cognit.engine.mermaid._which", fake_which)

    inspect_count = {"n": 0}

    def fake_run(args: list[str], **kwargs: object) -> _FakeCompleted:
        if "inspect" in args:
            inspect_count["n"] += 1
            return _FakeCompleted(0)
        if "run" in args:
            return _FakeCompleted(0)
        return _FakeCompleted(1)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert is_valid_mermaid("flowchart LR\nA --> B", strict=False) is True
    assert is_valid_mermaid("flowchart LR\nC --> D", strict=False) is True
    assert is_valid_mermaid("flowchart LR\nE --> F", strict=False) is True
    assert inspect_count["n"] == 1, (
        f"expected exactly 1 image inspect call, got {inspect_count['n']}"
    )


def test_docker_layer_builds_image_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """First-use behaviour: `docker image inspect` says "not found" (rc=1), so we
    call `docker build` to create it, then run. The build is one-time."""

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/docker" if cmd == "docker" else None

    monkeypatch.setattr("cognit.engine.mermaid._which", fake_which)

    seen: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> _FakeCompleted:
        op = next((a for a in args if a in ("inspect", "build", "run")), "?")
        seen.append(op)
        if op == "inspect":
            return _FakeCompleted(1)  # image missing
        if op == "build":
            return _FakeCompleted(0)  # build succeeded
        if op == "run":
            return _FakeCompleted(0)
        return _FakeCompleted(1)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert is_valid_mermaid("flowchart LR\nA --> B", strict=False) is True
    assert "inspect" in seen
    assert "build" in seen
    assert "run" in seen
    # And in the right order.
    assert seen.index("inspect") < seen.index("build") < seen.index("run")
