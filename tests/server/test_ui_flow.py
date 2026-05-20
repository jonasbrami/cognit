"""Playwright integration tests for the question → results → published flow."""
import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        try:
            yield pg
        finally:
            ctx.close()
            browser.close()


def test_initial_load_shows_questions(live_server, page) -> None:
    base, _posted = live_server
    page.goto(base, wait_until="networkidle")
    # 4 questions, each labeled "Question N"
    cards = page.locator("#questions-root .file")
    assert cards.count() == 4
    for i in range(1, 5):
        head = cards.nth(i - 1).locator(".file__head")
        assert f"Question {i}" in head.text_content()
    # type pills reflect question type (in fixture order: mcq, mermaid, open, tf)
    assert "multiple choice" in cards.nth(0).locator(".file__type").text_content().lower()
    assert "diagram" in cards.nth(1).locator(".file__type").text_content().lower()
    assert "open" in cards.nth(2).locator(".file__type").text_content().lower()
    assert "true / false" in cards.nth(3).locator(".file__type").text_content().lower()
    # mermaid Q has 2 diagram cards rendered
    assert cards.nth(1).locator(".diagram").count() == 2
    # open Q has a textarea
    assert cards.nth(2).locator("textarea").count() == 1
    # reviewbar in submit state with a Submit button
    bar = page.locator("#reviewbar")
    assert bar.locator("button").get_by_text("Submit", exact=False).is_visible()


def test_mcq_selection_toggles_class(live_server, page) -> None:
    base, _posted = live_server
    page.goto(base, wait_until="networkidle")
    first_q = page.locator("#questions-root .file").first
    opts = first_q.locator(".option")
    assert opts.count() == 4
    opts.nth(1).click()
    assert "selected" in (opts.nth(1).get_attribute("class") or "")
    assert "selected" not in (opts.nth(0).get_attribute("class") or "")


def test_submit_renders_results(live_server, page) -> None:
    base, _posted = live_server
    page.goto(base, wait_until="networkidle")

    # answer all 4 questions
    # Q1 mcq — pick option B (the correct one in fixture)
    page.locator("#questions-root .file").nth(0).locator(".option").nth(1).click()
    # Q2 mermaid — pick diagram A (correct)
    page.locator("#questions-root .file").nth(1).locator(".diagram").first.click()
    # Q3 open — type text
    page.locator("#questions-root .file").nth(2).locator("textarea").fill(
        "Redis is shared state across worker processes."
    )
    # Q4 tf — pick False (correct in fixture)
    page.locator("#questions-root .file").nth(3).locator(".tf__cell").nth(1).click()

    # submit
    page.locator("#reviewbar button").click()
    page.wait_for_selector("#questions-root .summary", timeout=5000)

    # summary card present with total score
    assert page.locator("#questions-root .summary").is_visible()
    summary = page.locator("#questions-root .summary").text_content()
    assert "95" in summary  # (100 + 100 + 80 + 100) / 4 = 95 — FakeLLM gives open=80

    # per-question result cards
    results = page.locator("#questions-root .file")
    assert results.count() == 4
    # at least 3 ok cards (the deterministic correct ones)
    assert page.locator("#questions-root .file.ok").count() >= 3
    # reviewbar swapped to publish state
    bar = page.locator("#reviewbar")
    assert "publish" in bar.text_content().lower()
