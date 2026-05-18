from quizz.engine.mermaid import is_valid_mermaid, MermaidUnavailable
import pytest
import shutil


@pytest.mark.skipif(not shutil.which("mmdc"), reason="mmdc not installed")
def test_valid_diagram():
    assert is_valid_mermaid("flowchart LR\nA --> B")


@pytest.mark.skipif(not shutil.which("mmdc"), reason="mmdc not installed")
def test_invalid_diagram():
    assert not is_valid_mermaid("not actually mermaid {{{")


def test_raises_if_mmdc_missing(monkeypatch):
    monkeypatch.setattr("quizz.engine.mermaid._which_mmdc", lambda: None)
    with pytest.raises(MermaidUnavailable):
        is_valid_mermaid("flowchart LR\nA --> B", strict=True)


def test_python_side_check_accepts_valid_when_mmdc_missing(monkeypatch):
    """When mmdc is missing, the lightweight Python-side check is the only gate.
    Valid-looking mermaid should still pass so we don't block legitimate diagrams."""
    monkeypatch.setattr("quizz.engine.mermaid._which_mmdc", lambda: None)
    assert is_valid_mermaid("flowchart LR\n  A --> B", strict=False) is True


def test_python_side_check_rejects_obvious_garbage_when_mmdc_missing(monkeypatch):
    """Previously, missing mmdc meant zero validation — Claude could emit anything and
    we'd accept it silently. The Python-side check now catches obvious failures even
    without mmdc: empty source, missing diagram-type header, grossly unbalanced brackets."""
    monkeypatch.setattr("quizz.engine.mermaid._which_mmdc", lambda: None)
    assert is_valid_mermaid("anything", strict=False) is False
    assert is_valid_mermaid("", strict=False) is False
    assert is_valid_mermaid("flowchart LR\n[[[[[[", strict=False) is False
