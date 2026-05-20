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


@pytest.mark.xfail(reason="renderer not implemented until Task 5", strict=True)
def test_initial_load_shows_questions(live_server, page) -> None:
    base, _posted = live_server
    page.goto(base, wait_until="networkidle")
    # the shell rendered
    assert page.locator(".topbar").is_visible()
    assert page.locator(".repohead").is_visible()
    # questions root is populated
    assert page.locator("#questions-root .file").count() == 4  # 4 questions in fixture
    # reviewbar starts in submit state
    assert page.locator("#reviewbar").is_visible()
    assert "submit" in page.locator("#reviewbar").text_content().lower()
