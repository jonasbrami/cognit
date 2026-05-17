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


def test_skip_if_missing_returns_true_by_default(monkeypatch):
    monkeypatch.setattr("quizz.engine.mermaid._which_mmdc", lambda: None)
    assert is_valid_mermaid("anything", strict=False) is True
