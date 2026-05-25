from cognit.mcp.validate import validate_and_prepare


def _mcq(qid="q1"):
    return {"type": "mcq", "id": qid, "prompt": "p", "options": ["A", "B"],
            "answer": "A", "explanation": "because A"}


def _good_mermaid():
    return {"type": "mermaid", "id": "m1", "prompt": "which flow?",
            "options": {
                "A": "flowchart LR\nA[req]-->B[auth]-->C[route]",
                "B": "flowchart LR\nA[req]-->B[route]-->C[auth]",
                "C": "flowchart LR\nA[req]-->B[auth]-->C[cache]",
                "D": "flowchart LR\nA[req]-->B[cache]-->C[auth]",
            },
            "answer": "A", "explanation": "auth precedes routing"}


def test_valid_quiz_returns_quiz_no_failures():
    quiz, failures = validate_and_prepare({"version": "1", "questions": [_mcq()]}, pr_number=7)
    assert failures == []
    assert quiz is not None and quiz.pr_number == 7


def test_mermaid_wrong_option_count_fails():
    m = _good_mermaid()
    m["options"].pop("D")
    quiz, failures = validate_and_prepare({"version": "1", "questions": [m]}, pr_number=7)
    assert quiz is None
    assert any("exactly 4 options" in f for f in failures)


def test_missing_explanation_fails():
    m = _mcq()
    m["explanation"] = ""
    quiz, failures = validate_and_prepare({"version": "1", "questions": [m]}, pr_number=7)
    assert quiz is None
    assert any("explanation" in f for f in failures)


def test_malformed_shape_fails():
    quiz, failures = validate_and_prepare({"version": "1", "questions": [{"type": "mcq"}]}, pr_number=7)
    assert quiz is None
    assert any("malformed" in f for f in failures)


def test_valid_mermaid_answer_survives_shuffle():
    q, failures = validate_and_prepare({"version": "1", "questions": [_good_mermaid()]}, pr_number=7)
    assert failures == []
    mq = q.questions[0]
    assert mq.answer in mq.options
    assert mq.explanation == "auth precedes routing"  # preserved through the shuffle
    assert q.pr_number == 7 and len(q.questions) == 1
