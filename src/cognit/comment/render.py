from cognit.engine.models import (
    Quiz,
    Answers,
    Results,
    MCQQuestion,
    MermaidQuestion,
    TrueFalseQuestion,
)

_MARKER_QUIZ = "<!-- cognit:quiz v1 -->"
_MARKER_ANSWERS = "<!-- cognit:answers v1 -->"
_MARKER_RESULTS = "<!-- cognit:results v1 -->"


def render_quiz(quiz: Quiz) -> str:
    parts: list[str] = [
        _MARKER_QUIZ,
        "## Quiz on your PR",
        "",
        "Take it: `cognit take` or scroll down.",
        "",
    ]
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
    lines: list[str] = [
        _MARKER_RESULTS,
        "## Quiz results",
        "",
        f"**Total: {res.total_score}%**",
        "",
    ]
    for r in res.per_question:
        icon = "✅" if r.correct else "❌"
        lines.append(f"- {icon} `{r.question_id}` — {r.score}%")
        if r.feedback:
            lines.append(f"  > {r.feedback}")
    lines.append("")
    lines.append("---")
    lines.append("<details><summary>Results state (used by the CLI)</summary>")
    lines.append("")
    lines.append("```json")
    lines.append(res.model_dump_json(indent=2))
    lines.append("```")
    lines.append("</details>")
    return "\n".join(lines)


def render_results_inlined(quiz: Quiz, answers: Answers, results: Results) -> str:
    """Render results with question prompts and author answers inlined.

    The in-memory-only flow no longer posts a quiz comment to the PR, so the published
    results comment must be self-contained. Each question is rendered with its prompt,
    the author's answer, the score, and any feedback. The JSON state block at the
    bottom is preserved so `parse_results` still round-trips.
    """
    answer_by_qid = {e.question_id: e.value for e in answers.entries}
    result_by_qid = {r.question_id: r for r in results.per_question}

    lines: list[str] = [
        _MARKER_RESULTS,
        "## Quiz results",
        "",
        f"**Total: {results.total_score}%**",
        "",
    ]
    for i, q in enumerate(quiz.questions, 1):
        r = result_by_qid.get(q.id)
        if r is None:
            continue
        icon = "✅" if r.correct else "❌"
        lines.append(f"### Question {i} — {icon} {r.score}%")
        lines.append("")
        lines.append(f"**Prompt:** {q.prompt}")
        lines.append("")
        user_answer = answer_by_qid.get(q.id, "")
        lines.append(f"**Your answer:** `{user_answer}`")
        if r.feedback:
            lines.append("")
            lines.append(f"> {r.feedback}")
        lines.append("")
    lines.append("---")
    lines.append("<details><summary>Results state (used by the CLI)</summary>")
    lines.append("")
    lines.append("```json")
    lines.append(results.model_dump_json(indent=2))
    lines.append("```")
    lines.append("</details>")
    return "\n".join(lines)
