from importlib import resources


def _load(name: str) -> str:
    return resources.files("cognit.engine.prompts").joinpath(name).read_text()


def test_system_generate_has_quality_anchors():
    sys_prompt = _load("system_generate.txt")
    assert "lookup test" in sys_prompt
    assert "usefulness check" in sys_prompt.lower()
    assert "explanation" in sys_prompt  # Output section requires it (enforced in Phase 3)
    assert "progression" in sys_prompt.lower()  # questions are ordered, not a bag
    assert "set_quiz" in sys_prompt


def test_system_grade_loads():
    assert "Judge ideas" in _load("system_grade.txt")
