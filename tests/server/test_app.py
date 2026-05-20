from fastapi.testclient import TestClient

from quizz.engine.llm_fake import FakeLLM
from quizz.engine.models import MCQQuestion, OpenQuestion, Quiz
from quizz.server.app import build_app


def _sample_quiz() -> Quiz:
    return Quiz(
        pr_number=42,
        questions=[
            MCQQuestion(id="q1", prompt="why?", options=["A", "B"], answer="A"),
            OpenQuestion(id="q2", prompt="explain", rubric="r"),
        ],
    )


def _noop_llm() -> FakeLLM:
    """Default test LLM: open-question grading returns a fixed canned score."""
    return FakeLLM(canned_open_score=85, canned_open_feedback="solid")


def test_get_root_renders_quiz() -> None:
    """The HTML shell loads with the github-native chrome and embeds the quiz JSON."""
    app = build_app(
        quiz=_sample_quiz(),
        pr_url="https://github.com/o/r/pull/42",
        llm=_noop_llm(),
        post_comment=lambda md: "https://x/y#1",
    )
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "<!doctype html>" in html.lower()
    # quiz JSON is injected
    assert '"pr_number": 42' in html or '"pr_number":42' in html
    assert "why?" in html  # the q1 prompt
    # github-native shell markers
    assert 'class="topbar"' in html
    assert 'class="repohead"' in html
    assert 'class="tabs"' in html
    assert 'id="questions-root"' in html
    assert 'id="reviewbar"' in html
    # the topbar says "quizz" not "GitHub" (decision #4)
    assert ">quizz<" in html
    # PR url linked in the header
    assert "https://github.com/o/r/pull/42" in html


def test_static_assets_served() -> None:
    app = build_app(
        quiz=_sample_quiz(),
        pr_url="x",
        llm=_noop_llm(),
        post_comment=lambda md: "https://x/y#unused",
    )
    client = TestClient(app)
    assert client.get("/static/quiz.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_submit_grades_everything_no_autopost() -> None:
    """POST /submit grades deterministic + LLM open Q, returns full Results, posts NOTHING."""
    posted: list[str] = []

    def _post(md: str) -> str:
        posted.append(md)
        return "https://x/y#unused"

    app = build_app(
        quiz=_sample_quiz(),
        pr_url="x",
        llm=FakeLLM(canned_open_score=85, canned_open_feedback="solid"),
        post_comment=_post,
    )
    client = TestClient(app)
    payload = {
        "version": "1",
        "pr_number": 42,
        "entries": [
            {"question_id": "q1", "value": "A"},  # correct
            {"question_id": "q2", "value": "good answer"},  # open: LLM-scored 85
        ],
    }
    r = client.post("/submit", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["total_score"] == 92  # (100 + 85) / 2 = 92 (integer floor)
    by_id = {q["question_id"]: q for q in data["per_question"]}
    assert by_id["q1"]["score"] == 100 and by_id["q1"]["correct"] is True
    assert by_id["q2"]["score"] == 85 and by_id["q2"]["feedback"] == "solid"
    # CRUCIAL: nothing posted yet — publishing is opt-in.
    assert posted == []


def test_publish_posts_results_comment() -> None:
    """POST /publish takes a Results payload and posts it as a PR comment."""
    posted: list[str] = []

    def _post(md: str) -> str:
        posted.append(md)
        return "https://x/y#1"

    app = build_app(
        quiz=_sample_quiz(),
        pr_url="x",
        llm=_noop_llm(),
        post_comment=_post,
    )
    client = TestClient(app)
    results_payload = {
        "version": "1",
        "pr_number": 42,
        "total_score": 92,
        "per_question": [
            {"question_id": "q1", "correct": True, "score": 100, "feedback": ""},
            {"question_id": "q2", "correct": True, "score": 85, "feedback": "solid"},
        ],
    }
    r = client.post("/publish", json=results_payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["total_score"] == 92
    assert body["comment_url"] == "https://x/y#1"
    assert len(posted) == 1
    assert "<!-- quizz:results v1 -->" in posted[0]
    assert "92%" in posted[0]


def test_submit_then_publish_round_trip() -> None:
    """End-to-end: submit returns results, then publishing those same results posts."""
    posted: list[str] = []

    def _post(md: str) -> str:
        posted.append(md)
        return "https://x/y#2"

    app = build_app(
        quiz=_sample_quiz(),
        pr_url="x",
        llm=FakeLLM(canned_open_score=60, canned_open_feedback="ok"),
        post_comment=_post,
    )
    client = TestClient(app)
    submit_resp = client.post(
        "/submit",
        json={
            "version": "1",
            "pr_number": 42,
            "entries": [
                {"question_id": "q1", "value": "B"},  # wrong
                {"question_id": "q2", "value": "meh"},
            ],
        },
    )
    assert submit_resp.status_code == 200
    assert posted == []  # nothing posted yet

    results = submit_resp.json()
    publish_resp = client.post("/publish", json=results)
    assert publish_resp.status_code == 200
    assert len(posted) == 1
    assert "<!-- quizz:results v1 -->" in posted[0]


def test_styles_css_has_expected_sections() -> None:
    """Stylesheet is served and organized per the spec's component inventory."""
    app = build_app(
        quiz=_sample_quiz(),
        pr_url="x",
        llm=_noop_llm(),
        post_comment=lambda md: "x",
    )
    client = TestClient(app)
    r = client.get("/static/styles.css")
    assert r.status_code == 200
    css = r.text
    # token block + key section markers
    for marker in [
        "/* tokens",
        "/* topbar",
        "/* repohead",
        "/* card",
        "/* reviewbar",
        "/* summary",
        "/* feedback",
        "/* banner",
        "/* responsive",
        "--blue",
        "--fg",
        "JetBrains Mono",
    ]:
        assert marker in css, f"missing CSS marker: {marker!r}"


def test_publish_returns_comment_url() -> None:
    """POST /publish returns the URL of the posted comment so the UI can link to it."""
    app = build_app(
        quiz=_sample_quiz(),
        pr_url="https://github.com/o/r/pull/42",
        llm=_noop_llm(),
        post_comment=lambda md: "https://github.com/o/r/pull/42#issuecomment-9999",
    )
    client = TestClient(app)
    results_payload = {
        "version": "1",
        "pr_number": 42,
        "total_score": 92,
        "per_question": [
            {"question_id": "q1", "correct": True, "score": 100, "feedback": ""},
            {"question_id": "q2", "correct": True, "score": 85, "feedback": "solid"},
        ],
    }
    r = client.post("/publish", json=results_payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["total_score"] == 92
    assert body["comment_url"] == "https://github.com/o/r/pull/42#issuecomment-9999"
