from quizz.engine.models import (
    Quiz, Answers, Results,
    MCQQuestion, MermaidQuestion, OpenQuestion, TrueFalseQuestion,
)

_MARKER_QUIZ = "<!-- quizz:quiz v1 -->"
_MARKER_ANSWERS = "<!-- quizz:answers v1 -->"
_MARKER_RESULTS = "<!-- quizz:results v1 -->"


def render_quiz(quiz: Quiz) -> str:
    parts: list[str] = [_MARKER_QUIZ, "## Quiz on your PR", "", "Take it: `quizz take` or scroll down.", ""]
    for i, q in enumerate(quiz.questions, 1):
        parts.append(f"### Question {i} — {q.type}")
        parts.append(q.prompt)
        parts.append("")
        if isinstance(q, MCQQuestion):
            for label in q.options:
                parts.append(f"- {label}")
        elif isinstance(q, MermaidQuestion):
            for label, src in q.options.items():
                parts.append(f"#### Option {label}")
                parts.append("```mermaid")
                parts.append(src)
                parts.append("```")
        elif isinstance(q, TrueFalseQuestion):
            parts.append("- true / false")
        # open: just the prompt; no extra rendering
        parts.append("")
    parts.append("---")
    parts.append("<details><summary>Quiz state (used by the CLI)</summary>")
    parts.append("")
    parts.append("```json")
    parts.append(quiz.model_dump_json(indent=2))
    parts.append("```")
    parts.append("</details>")
    return "\n".join(parts)


def render_answers(ans: Answers, deterministic_score: int) -> str:
    return (
        f"{_MARKER_ANSWERS}\n"
        f"## My answers\n\n"
        f"Deterministic-grade score (MCQ + mermaid + T/F): **{deterministic_score}%**\n\n"
        f"```json\n{ans.model_dump_json(indent=2)}\n```\n"
    )


def render_results(res: Results) -> str:
    lines: list[str] = [_MARKER_RESULTS, "## Quiz results", "", f"**Total: {res.total_score}%**", ""]
    for r in res.per_question:
        icon = "✅" if r.correct else "❌"
        lines.append(f"- {icon} `{r.question_id}` — {r.score}%")
        if r.feedback:
            lines.append(f"  > {r.feedback}")
    return "\n".join(lines)
