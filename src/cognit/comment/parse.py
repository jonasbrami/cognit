import re
from cognit.engine.models import Quiz, Answers, Results, QuestionResult

_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _extract_json(md: str, marker: str) -> str:
    if marker not in md:
        raise ValueError(f"marker {marker!r} not found")
    after = md.split(marker, 1)[1]
    m = _JSON_BLOCK.search(after)
    if not m:
        raise ValueError(f"no json block after {marker!r}")
    return m.group(1)


def parse_quiz(md: str) -> Quiz:
    return Quiz.model_validate_json(_extract_json(md, "<!-- cognit:quiz v1 -->"))


def parse_answers(md: str) -> Answers:
    return Answers.model_validate_json(_extract_json(md, "<!-- cognit:answers v1 -->"))


def parse_results(md: str) -> Results:
    """Parse a results comment. Prefers the embedded JSON state; falls back to scraping the human text."""
    marker = "<!-- cognit:results v1 -->"
    if marker not in md:
        raise ValueError("not a results comment")
    # Prefer JSON state if present (added in v1; older comments may lack it).
    try:
        return Results.model_validate_json(_extract_json(md, marker))
    except ValueError:
        pass  # No JSON block; fall back to scraping.
    total = 0
    m = re.search(r"\*\*Total:\s*(\d+)%\*\*", md)
    if m:
        total = int(m.group(1))
    per: list[QuestionResult] = []
    for line in md.splitlines():
        m2 = re.match(r"- (✅|❌) `([^`]+)` — (\d+)%", line)
        if m2:
            per.append(
                QuestionResult(
                    question_id=m2.group(2),
                    correct=m2.group(1) == "✅",
                    score=int(m2.group(3)),
                    feedback="",
                )
            )
    return Results(pr_number=0, total_score=total, per_question=per)
