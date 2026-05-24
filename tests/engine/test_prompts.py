from importlib import resources


def _load(name: str) -> str:
    return resources.files("cognit.engine.prompts").joinpath(name).read_text()


def test_generate_txt_formats_with_all_placeholders():
    # generate.txt is .format()-ed in draft_quiz; missing/extra braces would raise.
    out = _load("generate.txt").format(
        pr_number=14,
        branch="feat/x",
        pr_title="title",
        pr_body="body",
        diff_overview="src/a.py | +1 -0",
    )
    assert "PR #14" in out
    assert "Plan before drafting" in out


def test_system_generate_has_quality_anchors():
    sys_prompt = _load("system_generate.txt")
    assert "lookup test" in sys_prompt
    assert "usefulness check" in sys_prompt.lower()
    assert "explanation" in sys_prompt  # Output section requires it (enforced in Phase 3)


def test_system_grade_loads():
    assert "Judge ideas" in _load("system_grade.txt")
