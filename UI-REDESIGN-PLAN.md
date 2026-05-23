# UI redesign — github-native edition · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the editorial UI in `src/cognit/server/assets/` with the github-native design specified in `UI-REDESIGN.md`. Keep all engine/CLI behavior; add one backend field (`comment_url` on `/publish`) needed by the new success banner.

**Architecture:** Three single-page-app states (`questions` → `results` → `published`) rendered by a rewritten `quiz.js` against a new HTML shell and stylesheet. The visual design is already prototyped and battle-tested in `mockups/github.html` · `mockups/results.html` · `mockups/published.html` — implementation lifts CSS/markup from the mocks and wires them to live data.

**Tech Stack:** FastAPI · Pydantic v2 · vanilla JS (no framework) · CSS custom properties (Primer-ish tokens) · JetBrains Mono · mermaid.js (UMD bundle, already in `assets/`) · pytest + Playwright for the integration test.

**Reference docs:** Read these before starting:
- `UI-REDESIGN.md` — design spec, decisions, component inventory
- `mockups/github.html`, `mockups/results.html`, `mockups/published.html` — battle-tested visual targets
- `INTENTS.md` — product intent (don't lose the "failing-doesn't-block" framing)

---

## Task 1: Capture comment URL through `/publish`

**Files:**
- Modify: `src/cognit/ghio/pr.py:42-46` (`post_comment` returns URL)
- Modify: `src/cognit/server/app.py:30-77` (callback signature + `/publish` response)
- Modify: `src/cognit/cli/take.py:65,94` (callback wiring)
- Modify: `tests/server/test_app.py` (callback signature in test fixtures + new shape assertion)

**Context:** Today `post_comment` shells out to `gh pr comment` which prints nothing useful to stdout. To get the URL of the created comment we switch to `gh api repos/{owner}/{repo}/issues/{n}/comments -f body=...` which returns JSON with an `html_url` field. The `Callable[[str], None]` callback becomes `Callable[[str], str]`.

- [ ] **Step 1.1: Write failing test for `/publish` returning `comment_url`**

Append to `tests/server/test_app.py` (use the existing `_sample_quiz` / `_noop_llm` helpers already in the file):

```python
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
```

Also update the existing `test_publish_posts_results_comment` in the same file so its `post_comment` lambda returns a string (otherwise it'll crash once the callback type tightens):

```python
post_comment=lambda md: (posted.append(md), "https://x/y#1")[1],
```

- [ ] **Step 1.2: Run new test — expect FAIL**

```bash
uv run pytest tests/server/test_app.py::test_publish_returns_comment_url -v
```

Expected: FAIL — `KeyError: 'comment_url'` (response only has `{ok, total_score}` today).

- [ ] **Step 1.3: Change `post_comment` to return URL (and switch to `gh api`)**

Replace `post_comment` in `src/cognit/ghio/pr.py` (around line 42):

```python
def post_comment(pr_url_or_number: str, body: str) -> str:
    """Post a comment to a PR. Returns the html_url of the created comment."""
    info = fetch_pr_info(pr_url_or_number)
    owner, name = info.repo.split("/", 1)
    result = subprocess.run(
        [
            "gh", "api",
            f"repos/{owner}/{name}/issues/{info.number}/comments",
            "-f", f"body={body}",
            "--jq", ".html_url",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
```

- [ ] **Step 1.4: Update callback type and `/publish` handler in `app.py`**

In `src/cognit/server/app.py`:

```python
# line 30 (build_app signature) — tighten the type:
def build_app(
    *,
    quiz: Quiz,
    pr_url: str,
    llm: LLMClient,
    post_comment: Callable[[str], str],  # returns the comment's html_url
) -> FastAPI:
```

```python
# replace the publish handler (around line 70):
@app.post("/publish")
async def publish(req: Request) -> JSONResponse:
    body = await req.json()
    results = Results.model_validate(body)
    comment_url = post_comment(render_results(results))
    return JSONResponse(
        {"ok": True, "total_score": results.total_score, "comment_url": comment_url}
    )
```

- [ ] **Step 1.5: Update CLI wiring in `take.py`**

In `src/cognit/cli/take.py`:

```python
# line 65 — tighten signature:
post_comment_fn: Callable[[str], str],
```

```python
# line 94 — partial application already returns the string now:
post_comment_fn=lambda md: post_comment(pr_url, md),
```

(The lambda already returns whatever `post_comment` returns, which is now `str` — no body change needed beyond the type annotation.)

- [ ] **Step 1.6: Run all server + CLI tests**

```bash
uv run pytest tests/server/ tests/test_smoke.py -v
```

Expected: all pass, including the new `test_publish_returns_comment_url`.

- [ ] **Step 1.7: Commit**

```bash
git add src/cognit/ghio/pr.py src/cognit/server/app.py src/cognit/cli/take.py tests/server/test_app.py
git commit -m "feat(server): /publish returns comment_url; switch to gh api for posting"
```

---

## Task 2: Replace `index.html` with github-native shell

**Files:**
- Modify: `src/cognit/server/assets/index.html` (full replacement, was 60 lines, becomes ~80)
- Modify: `tests/server/test_app.py` (extend `test_get_root_renders_quiz` to assert new shell)

**Context:** The new shell mimics a GitHub PR page (topbar / repo header / tab strip / main+sidebar grid / sticky review bar) but is branded "cognit" (decision #4 in spec). The container `<main id="quiz">` and `<section id="result">` from today's HTML go away — replaced by `<main id="questions-root">` (questions state) and result-state DOM rendered inline by JS. Template substitutions stay the same: `__PR__`, `__PR_URL__`, `__QUIZ_JSON__`.

- [ ] **Step 2.1: Write failing test for new shell structure**

Replace `test_get_root_renders_quiz` in `tests/server/test_app.py`:

```python
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
    # the topbar says "cognit" not "GitHub" (decision #4)
    assert ">cognit<" in html
    # PR url linked in the header
    assert "https://github.com/o/r/pull/42" in html
```

- [ ] **Step 2.2: Run — expect FAIL**

```bash
uv run pytest tests/server/test_app.py::test_get_root_renders_quiz -v
```

Expected: FAIL — none of the new class/id markers are in the old editorial HTML.

- [ ] **Step 2.3: Replace `index.html`**

Rewrite `src/cognit/server/assets/index.html` to this exact content:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>cognit · PR #__PR__</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap">
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>

  <!-- decorative topbar: borrows GitHub chrome, branded cognit -->
  <header class="topbar">
    <span class="topbar__logo" aria-hidden="true"></span>
    <span class="topbar__brand">cognit</span>
    <span class="topbar__spacer"></span>
    <span class="topbar__pr-link"><a href="__PR_URL__">#__PR__ on GitHub ↗</a></span>
  </header>

  <!-- repo header + tab strip (decorative chrome, "Author quiz" is the only active tab) -->
  <section class="repohead">
    <div class="repohead__title">
      <span class="badge badge--quiz">▸ author quiz</span>
      <span>PR <a href="__PR_URL__">#__PR__</a> · failing doesn't block the merge</span>
    </div>
    <div class="tabs" role="tablist">
      <div class="tab tab--active" role="tab" aria-selected="true">Author quiz</div>
    </div>
  </section>

  <!-- main + sidebar grid; both populated by quiz.js -->
  <div class="container">
    <main id="questions-root" class="main"></main>
    <aside id="sidebar-root" class="sidebar"></aside>
  </div>

  <!-- sticky review bar (state-dependent: submit / publish / published) -->
  <footer id="reviewbar" class="reviewbar"></footer>

  <!-- mermaid UMD: attaches window.mermaid -->
  <script src="/static/mermaid.min.js"></script>
  <script>
    window.QUIZ = __QUIZ_JSON__;
    window.PR_URL = "__PR_URL__";
  </script>
  <script src="/static/quiz.js"></script>
</body>
</html>
```

- [ ] **Step 2.4: Run test — expect PASS**

```bash
uv run pytest tests/server/test_app.py::test_get_root_renders_quiz -v
```

Expected: PASS.

- [ ] **Step 2.5: Run all server tests to confirm nothing else broke**

```bash
uv run pytest tests/server/ -v
```

Expected: all pass. (Other server tests don't depend on specific HTML content.)

- [ ] **Step 2.6: Commit**

```bash
git add src/cognit/server/assets/index.html tests/server/test_app.py
git commit -m "feat(ui): github-native HTML shell (topbar, repohead, tabs, sidebar slot)"
```

---

## Task 3: Replace `styles.css` with github-native styles

**Files:**
- Modify: `src/cognit/server/assets/styles.css` (full replacement; was 861 lines, target ~700)

**Context:** The new CSS is already battle-tested across `mockups/github.html` + `mockups/results.html` + `mockups/published.html` (Playwright pass at desktop/720/mobile, zero console warnings, fonts load cleanly). The implementation copies that CSS into the production file, organized by the section list in `UI-REDESIGN.md`. The mocks each duplicate the same tokens — we deduplicate during the copy.

No unit test for CSS itself; verified at the integration level in Task 4 onward. We do verify the file is served and has the expected section structure.

- [ ] **Step 3.1: Write structural test for the stylesheet**

Append to `tests/server/test_app.py`:

```python
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
    # token block + key section markers (sectioned comments help future readers)
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
        "--blue",  # Primer-ish accent
        "--fg",
        "JetBrains Mono",
    ]:
        assert marker in css, f"missing CSS marker: {marker!r}"
```

- [ ] **Step 3.2: Run — expect FAIL**

```bash
uv run pytest tests/server/test_app.py::test_styles_css_has_expected_sections -v
```

Expected: FAIL — old editorial CSS doesn't have those markers.

- [ ] **Step 3.3: Replace `styles.css`**

Copy the CSS rules out of `mockups/github.html`, `mockups/results.html`, `mockups/published.html` (inside their `<style>` blocks) into `src/cognit/server/assets/styles.css`, organized into the following sectioned structure. Each section header is the exact comment string the test checks for.

```css
/* tokens ─────────────────────────────────────────────────────────── */
:root {
  --bg:           #ffffff;
  --bg-subtle:    #f6f8fa;
  --bg-canvas:    #ffffff;
  --border:       #d1d9e0;
  --border-soft:  #d8dee4;
  --border-mute:  #eaeef2;
  --fg:           #1f2328;
  --fg-mute:      #59636e;
  --fg-faint:     #818b96;
  --blue:         #0969da;
  --blue-bg:      #ddf4ff;
  --blue-border:  #54aeff;
  --green:        #1a7f37;
  --green-dim:    #1f883d;
  --green-bg:     #dafbe1;
  --green-soft:   #aceebb;
  --red:          #cf222e;
  --red-bg:       #ffebe9;
  --red-soft:     #ff8182;
  --orange:       #bc4c00;
  --orange-bg:    #fff1e5;
  --orange-soft:  #f5b969;
  --purple:       #8250df;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif, "Apple Color Emoji";
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* base */
* { box-sizing: border-box; }
html, body { background: var(--bg-canvas); overflow-x: clip; }
body { margin: 0; color: var(--fg); font-family: var(--sans); font-size: 14px; line-height: 1.5; }
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }

/* topbar ─────────────────────────────────────────────────────────── */
/* (copy from mockups/github.html `.topbar` rules; replace `nav` markup with `.topbar__brand` and `.topbar__pr-link`) */

/* repohead ───────────────────────────────────────────────────────── */
/* (copy from mockups; tab strip with overflow-x:auto + flex-shrink:0 on .tab) */

/* container ─────────────────────────────────────────────────────── */
/* .container, .main, .sidebar grid; sidebar stacks above main at <=900px */

/* card ──────────────────────────────────────────────────────────── */
/* shared .file chrome — used for question cards AND result cards */
/* result variant: .file.ok / .file.bad / .file.mid (colored left border) */

/* inputs/mcq ────────────────────────────────────────────────────── */
/* .option (row) + .option__radio + .option__key + .option__text + selected state */

/* inputs/mermaid ────────────────────────────────────────────────── */
/* .diagrams grid (2 cols desktop, 1 col mobile)
   .diagram card + selected state + .user-bad + .correct variants
   plus mermaid SVG overrides: node strokes use var(--blue), labels use var(--mono) and var(--fg) */

/* inputs/open ───────────────────────────────────────────────────── */
/* textarea.open with blue focus ring, char-count meta below */

/* inputs/tf ─────────────────────────────────────────────────────── */
/* .tf pair (inline-flex), .tf .sel uses var(--blue) bg */

/* sidebar ───────────────────────────────────────────────────────── */
/* .side-block, .side-title, .sidelist (with .ic.ok/.bad/.mid),
   .progress dots, .side-score (big number), .timeline */

/* summary ───────────────────────────────────────────────────────── */
/* .summary card + .summary__ring (CSS conic-gradient, --c: var(--fg) — single ink color per decision #3) */
/* .summary__pips strip with .ok/.bad/.mid tints */

/* feedback ──────────────────────────────────────────────────────── */
/* .feedback bubble (purple left border) + .feedback__head with avatar */

/* banner ────────────────────────────────────────────────────────── */
/* .banner (success state, green, with slidedown animation) */

/* reviewbar ─────────────────────────────────────────────────────── */
/* sticky bottom action bar — three variants by class:
   .reviewbar.is-submit (neutral)  → "Submit quiz" green primary
   .reviewbar.is-publish (neutral) → "Discard" + "Publish to PR"
   .reviewbar.is-published (green) → "Delete comment" + "Open on GitHub" */

/* posted-comment ─── omitted, decision #2 keeps the published view link-only */

/* responsive ────────────────────────────────────────────────────── */
/* @media (max-width: 900px) { .container { grid-template-columns: 1fr; } .sidebar { order: -1; } }
   @media (max-width: 700px) { reviewbar wrap, crossnav wrap }
   @media (max-width: 600px) { .banner CTA drops to its own row } */
```

**Important — score ring color (decision #3).** When copying the `.summary__ring` rule from the mocks, replace its `--c: var(--orange)` default with `--c: var(--fg)` and remove the per-band overrides. The ring stays a single ink color regardless of score.

**Important — mermaid restyle (spec §"Mermaid styling").** Replace the editorial blueprint overrides with:

```css
.diagram .mermaid .node rect,
.diagram .mermaid .node path { stroke: var(--blue); stroke-width: 1.5px; fill: #fff; }
.diagram .mermaid .edgePath .path { stroke: var(--fg); }
.diagram .mermaid .nodeLabel,
.diagram .mermaid .edgeLabel,
.diagram .mermaid foreignObject div { font-family: var(--mono); color: var(--fg); font-size: 12px; }
.diagram.user-bad .mermaid .node rect, .diagram.user-bad .mermaid .node path { stroke: var(--red); }
.diagram.correct .mermaid .node rect, .diagram.correct .mermaid .node path { stroke: var(--green); }
```

- [ ] **Step 3.4: Run structural test — expect PASS**

```bash
uv run pytest tests/server/test_app.py::test_styles_css_has_expected_sections -v
```

Expected: PASS.

- [ ] **Step 3.5: Manual sanity check the CSS is served**

```bash
uv run pytest tests/server/ -v
```

Then start the dev server briefly and hit `/static/styles.css`:

```bash
# in another shell, with the quiz server running locally for testing:
curl -sI http://localhost:8765/static/styles.css | head -3
```

(skip the curl if no easy dev server — the pytest covers serving)

- [ ] **Step 3.6: Commit**

```bash
git add src/cognit/server/assets/styles.css tests/server/test_app.py
git commit -m "feat(ui): github-native stylesheet (Primer tokens, ink score ring, ink-on-white mermaid)"
```

---

## Task 4: Playwright integration test scaffolding

**Files:**
- Create: `tests/server/test_ui_flow.py` (new — playwright integration test)
- Modify: `tests/conftest.py` (add `live_server` fixture)
- Modify: `pyproject.toml` (move `playwright` from dev to test deps if needed — confirm it's available to pytest)

**Context:** The remaining tasks need to drive a real browser against a real FastAPI server to assert that the JS renderers produce the right DOM in each state. This task lays the fixture so subsequent tasks (5, 6, 7) just add test functions.

- [ ] **Step 4.1: Add the `live_server` fixture**

Create or append to `tests/conftest.py`:

```python
import socket
import threading
import time
from collections.abc import Callable, Iterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from cognit.engine.llm_fake import FakeLLM
from cognit.engine.models import MCQQuestion, MermaidQuestion, OpenQuestion, Quiz, TrueFalseQuestion
from cognit.server.app import build_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(app: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # poll until ready
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=0.3) as c:
                if c.get(f"http://127.0.0.1:{port}/static/styles.css").status_code == 200:
                    return server, thread
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("uvicorn did not become ready within 5s")


@pytest.fixture
def sample_quiz() -> Quiz:
    return Quiz(
        pr_number=142,
        questions=[
            MCQQuestion(
                id="q1",
                prompt="When `rate_limit_exceeded(key)` returns True, the middleware…",
                options=[
                    "raises HTTPException(429)",
                    "returns JSONResponse(status_code=429) with a Retry-After header",
                    "logs a warning and passes through",
                    "increments a counter and continues",
                ],
                answer="returns JSONResponse(status_code=429) with a Retry-After header",
            ),
            MermaidQuestion(
                id="q2",
                prompt="Which diagram matches the actual request path?",
                options={
                    "A": "flowchart LR; A[req]-->B[auth]-->C[limit]-->D[route]",
                    "B": "flowchart LR; A[req]-->B[limit]-->C[auth]-->D[route]",
                },
                answer="A",
            ),
            OpenQuestion(id="q3", prompt="Why Redis over a dict?", rubric="cross-worker state"),
            TrueFalseQuestion(id="q4", prompt="`@skip_rate_limit` bypasses the middleware entirely.", answer=False),
        ],
    )


@pytest.fixture
def live_server(sample_quiz: Quiz) -> Iterator[tuple[str, list[str]]]:
    """Run the FastAPI app on a random local port. Yields (base_url, posted_bodies)."""
    posted: list[str] = []

    def fake_post(body: str) -> str:
        posted.append(body)
        return "https://github.com/jonas/cognit/pull/142#issuecomment-9999"

    app = build_app(
        quiz=sample_quiz,
        pr_url="https://github.com/jonas/cognit/pull/142",
        llm=FakeLLM(canned_open_score=80, canned_open_feedback="reasonable"),
        post_comment=fake_post,
    )
    port = _free_port()
    server, thread = _start_server(app, port)
    try:
        yield f"http://127.0.0.1:{port}", posted
    finally:
        server.should_exit = True
        thread.join(timeout=2)
```

- [ ] **Step 4.2: Write the first playwright test (smoke)**

Create `tests/server/test_ui_flow.py`:

```python
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
            browser.close()


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
```

- [ ] **Step 4.3: Run the smoke test — expect FAIL**

```bash
uv run pytest tests/server/test_ui_flow.py::test_initial_load_shows_questions -v
```

Expected: FAIL — `quiz.js` is still the editorial renderer and produces no `.file` elements. We'll fix in Task 5.

- [ ] **Step 4.4: Mark the test xfail temporarily and commit scaffolding**

Add `@pytest.mark.xfail(reason="renderer not implemented until Task 5", strict=True)` above the test for now, so CI is green on the scaffolding commit:

```python
@pytest.mark.xfail(reason="renderer not implemented until Task 5", strict=True)
def test_initial_load_shows_questions(live_server, page) -> None:
    ...
```

- [ ] **Step 4.5: Confirm scaffolding passes**

```bash
uv run pytest tests/server/test_ui_flow.py -v
```

Expected: 1 xfail.

- [ ] **Step 4.6: Commit**

```bash
git add tests/conftest.py tests/server/test_ui_flow.py
git commit -m "test(server): playwright fixture + live_server scaffolding for UI flow tests"
```

---

## Task 5: JS — question state renderer

**Files:**
- Modify: `src/cognit/server/assets/quiz.js` (rewrite render functions; keep submit/publish wiring shape)
- Modify: `tests/server/test_ui_flow.py` (remove xfail, add per-question structure assertions)

**Context:** The new `quiz.js` keeps the same module shape (reads `window.QUIZ`, defines DOM helpers, wires submit) but replaces `renderQuestion` with a function that builds the github-native `.file` card structure. Mermaid `.initialize()` switches to ink-on-white theme variables matching the new aesthetic. Questions are labeled `Question 1`, `Question 2`, etc. (decision #1 — no fake `.mcq` extensions, no Answered checkbox).

- [ ] **Step 5.1: Update test — assert per-question DOM**

In `tests/server/test_ui_flow.py`, replace the smoke test (remove `@pytest.mark.xfail`):

```python
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
```

- [ ] **Step 5.2: Run — expect FAIL**

```bash
uv run pytest tests/server/test_ui_flow.py -v
```

Expected: FAIL — current `quiz.js` produces the editorial `.question` DOM, not `.file`.

- [ ] **Step 5.3: Rewrite `quiz.js`**

Replace the contents of `src/cognit/server/assets/quiz.js`. Lift the option/diagram/textarea/tf markup from `mockups/github.html` and produce it with the `el()` DOM helper:

```javascript
// cognit front-end — github-native UI.
// Contracts:
//   - reads window.QUIZ (shape in server/engine/models.py: Quiz)
//   - reads window.PR_URL
//   - POSTs to /submit, then optionally /publish
//   - mermaid is loaded via UMD; window.mermaid present before this script runs

window.mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  fontFamily: '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace',
  themeVariables: {
    background: "transparent",
    primaryColor: "#ffffff",
    primaryBorderColor: "#0969da",
    primaryTextColor: "#1f2328",
    lineColor: "#1f2328",
    secondaryColor: "#f6f8fa",
    tertiaryColor: "#f6f8fa",
    fontSize: "12px",
  },
});

const quiz = window.QUIZ;
const questionsRoot = document.getElementById("questions-root");
const sidebarRoot = document.getElementById("sidebar-root");
const reviewbar = document.getElementById("reviewbar");

// answers state — { [question_id]: value }
const answers = {};

// cached after submit so Publish can re-send without re-grading
let lastResults = null;

// small DOM helper
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "for") node.htmlFor = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of (Array.isArray(children) ? children : [children])) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

const TYPE_LABEL = {
  mcq: "Multiple choice",
  mermaid: "Diagram · pick a flow",
  open: "Open · LLM graded",
  tf: "True / False",
};

const LETTER = ["A", "B", "C", "D", "E", "F"];

// ── question renderers ──────────────────────────────────────────

function renderMCQ(q) {
  return q.options.map((opt, i) =>
    el("div", {
      class: "option",
      onclick: (e) => {
        answers[q.id] = opt;
        e.currentTarget.parentElement.querySelectorAll(".option").forEach(o => o.classList.remove("selected"));
        e.currentTarget.classList.add("selected");
        updateReviewbarSubmit();
      },
    }, [
      el("div", { class: "option__radio" }),
      el("div", {}, [
        el("span", { class: "option__key", text: LETTER[i] }),
        el("span", { class: "option__text", text: opt }),
      ]),
    ])
  );
}

function renderMermaid(q) {
  const grid = el("div", { class: "diagrams" });
  Object.entries(q.options).forEach(([label, src]) => {
    const merm = el("div", { class: "mermaid" });
    merm.textContent = src;  // textContent only — never innerHTML (security)
    const card = el("div", {
      class: "diagram",
      onclick: (e) => {
        answers[q.id] = label;
        grid.querySelectorAll(".diagram").forEach(d => d.classList.remove("selected"));
        e.currentTarget.classList.add("selected");
        updateReviewbarSubmit();
      },
    }, [
      el("div", { class: "diagram__label", text: `diagram ${label}` }),
      merm,
    ]);
    grid.appendChild(card);
  });
  return [grid];
}

function renderOpen(q) {
  const ta = el("textarea", {
    class: "open",
    placeholder: "2–3 sentences. LLM grades after submit.",
    oninput: (e) => { answers[q.id] = e.target.value; updateReviewbarSubmit(); },
  });
  return [ta];
}

function renderTF(q) {
  const wrap = el("div", { class: "tf" });
  ["true", "false"].forEach((v) => {
    const cell = el("div", {
      class: "tf__cell",
      text: v.charAt(0).toUpperCase() + v.slice(1),
      onclick: (e) => {
        answers[q.id] = v;
        wrap.querySelectorAll(".tf__cell").forEach(c => c.classList.remove("sel"));
        e.currentTarget.classList.add("sel");
        updateReviewbarSubmit();
      },
    });
    wrap.appendChild(cell);
  });
  return [wrap];
}

function renderQuestion(q, i) {
  const inputsByType = { mcq: renderMCQ, mermaid: renderMermaid, open: renderOpen, tf: renderTF };
  const inputs = inputsByType[q.type](q);
  return el("article", { class: "file" }, [
    el("div", { class: "file__head" }, [
      el("div", { class: "file__title", text: `Question ${i + 1}` }),
      el("div", { class: "file__type", text: TYPE_LABEL[q.type] }),
    ]),
    el("div", { class: "file__body" }, [
      el("p", { class: "prompt", text: q.prompt }),
      ...inputs,
    ]),
  ]);
}

// ── sidebar (questions state) ───────────────────────────────────

function renderSidebar() {
  sidebarRoot.innerHTML = "";
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Progress" }),
    el("div", { class: "progress" },
      quiz.questions.map((_, i) => el("span", { class: "progress__dot", "data-i": String(i) }))
    ),
    el("div", { class: "progress-text", text: `0 of ${quiz.questions.length} answered` }),
  ]));
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Questions" }),
    el("ul", { class: "sidelist" },
      quiz.questions.map((q, i) => el("li", {}, [
        el("span", { class: "check empty", text: "○" }),
        ` Q${i + 1} · ${TYPE_LABEL[q.type].split(" ")[0].toLowerCase()}`,
      ]))
    ),
  ]));
}

function updateSidebarProgress() {
  const total = quiz.questions.length;
  const done = quiz.questions.filter(q => {
    const v = answers[q.id];
    return v != null && v !== "";
  }).length;
  sidebarRoot.querySelectorAll(".progress__dot").forEach((dot, i) => {
    dot.classList.toggle("done", i < done);
  });
  const txt = sidebarRoot.querySelector(".progress-text");
  if (txt) txt.textContent = `${done} of ${total} answered`;
  sidebarRoot.querySelectorAll(".sidelist .check").forEach((c, i) => {
    const v = answers[quiz.questions[i].id];
    if (v != null && v !== "") { c.textContent = "✓"; c.classList.remove("empty"); }
    else { c.textContent = "○"; c.classList.add("empty"); }
  });
}

// ── reviewbar — submit state ────────────────────────────────────

function updateReviewbarSubmit() {
  updateSidebarProgress();
}

function renderReviewbarSubmit() {
  reviewbar.className = "reviewbar is-submit";
  reviewbar.innerHTML = "";
  reviewbar.appendChild(el("div", { class: "reviewbar__msg" }, [
    "Failing the quiz won't block your merge. Open question grades after submit.",
  ]));
  reviewbar.appendChild(el("div", { class: "reviewbar__spacer" }));
  reviewbar.appendChild(el("button", {
    class: "btn btn--primary",
    type: "button",
    text: "Submit quiz",
    onclick: submitQuiz,
  }));
}

// ── flow ────────────────────────────────────────────────────────

function renderQuestions() {
  questionsRoot.innerHTML = "";
  quiz.questions.forEach((q, i) => questionsRoot.appendChild(renderQuestion(q, i)));
  renderSidebar();
  renderReviewbarSubmit();
  // render mermaid into any newly-attached .mermaid blocks
  window.mermaid.run({ querySelector: "#questions-root .mermaid" });
}

async function submitQuiz() {
  // implemented in Task 6
  console.log("submit pending", answers);
}

renderQuestions();
```

- [ ] **Step 5.4: Run the test — expect PASS**

```bash
uv run pytest tests/server/test_ui_flow.py -v
```

Expected: PASS for both tests in the file.

- [ ] **Step 5.5: Confirm existing server tests still pass**

```bash
uv run pytest tests/server/ -v
```

Expected: all pass.

- [ ] **Step 5.6: Commit**

```bash
git add src/cognit/server/assets/quiz.js tests/server/test_ui_flow.py
git commit -m "feat(ui): question-state renderer (file cards, ink mermaid theme, sidebar progress)"
```

---

## Task 6: JS — results state renderer + `/submit` wiring

**Files:**
- Modify: `src/cognit/server/assets/quiz.js` (add `renderResults`, real `submitQuiz`)
- Modify: `tests/server/test_ui_flow.py` (add results test)

**Context:** Clicking Submit POSTs `answers` to `/submit`, receives the `Results` payload, swaps `#questions-root` for the results layout: summary card + colored per-question result cards (green/red/orange left border, your-answer highlighted, correct-answer shown if wrong, LLM feedback bubble for open). Sidebar swaps to a Score block + per-question list with status icons. Reviewbar swaps to `Discard` + `Publish to PR`.

- [ ] **Step 6.1: Add results test**

Append to `tests/server/test_ui_flow.py`:

```python
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
    # at least one ok card and one mid (the open, scored 80)
    assert results.locator(".file.ok").count() >= 3  # 3 correct deterministic
    # reviewbar swapped to publish state
    bar = page.locator("#reviewbar")
    assert "publish" in bar.text_content().lower()
```

- [ ] **Step 6.2: Run — expect FAIL**

```bash
uv run pytest tests/server/test_ui_flow.py::test_submit_renders_results -v
```

Expected: FAIL — `submitQuiz` is a stub.

- [ ] **Step 6.3: Implement results renderer**

Append to `src/cognit/server/assets/quiz.js` (replace the `submitQuiz` stub at the bottom; add the new helpers above the flow section):

```javascript
// ── results-state renderers ─────────────────────────────────────

function scoreClass(score) {
  if (score >= 90) return "ok";
  if (score >= 60) return "mid";
  return "bad";
}

function renderSummary(results) {
  const total = results.total_score;
  const pips = results.per_question.map(r => {
    const cls = scoreClass(r.score);
    const glyph = cls === "ok" ? "✓" : cls === "bad" ? "✗" : "~";
    return el("span", { class: `pip pip--${cls}`, text: glyph });
  });
  return el("section", { class: "summary" }, [
    el("div", {
      class: "summary__ring",
      // single ink color regardless of score (decision #3)
      style: `--val: ${total}; --c: var(--fg);`,
    }, [
      el("div", { class: "summary__num", text: String(total) }, [
        el("small", { text: "/ 100" }),
      ]),
    ]),
    el("div", { class: "summary__body" }, [
      el("h2", { text: `Scored locally · ${results.per_question.filter(r => r.correct).length} of ${results.per_question.length} right` }),
      el("p", { text: "Below: per-question breakdown. The open answer is graded by the LLM." }),
      el("div", { class: "summary__pips" }, pips),
    ]),
  ]);
}

function renderResultCard(q, r, i) {
  const cls = scoreClass(r.score);
  const verdict = cls === "ok" ? "correct" : cls === "bad" ? "incorrect" : "partial";
  const body = [
    el("p", { class: "prompt", text: q.prompt }),
  ];
  // show user's answer + correct answer if wrong
  const userVal = answers[q.id];
  if (q.type === "mcq" || q.type === "tf") {
    body.push(el("div", { class: `ans-row user-${cls === "ok" ? "ok" : "bad"}` }, [
      el("div", { class: "ans-row__icon", text: cls === "ok" ? "✓" : "✗" }),
      el("div", { class: "ans-row__text", text: String(userVal) }),
      el("div", { class: "ans-row__tag", text: cls === "ok" ? "correct" : "your pick" }),
    ]));
    if (cls !== "ok") {
      body.push(el("div", { class: "ans-row correct" }, [
        el("div", { class: "ans-row__icon", text: "✓" }),
        el("div", { class: "ans-row__text", text: String(q.answer) }),
        el("div", { class: "ans-row__tag", text: "correct answer" }),
      ]));
    }
  } else if (q.type === "mermaid") {
    // show user's pick + correct (omit the rest)
    const wantLabels = new Set([userVal, q.answer].filter(Boolean));
    const grid = el("div", { class: "diagrams" });
    Object.entries(q.options).forEach(([label, src]) => {
      if (!wantLabels.has(label)) return;
      const isCorrect = label === q.answer;
      const isUserPick = label === userVal;
      const merm = el("div", { class: "mermaid" });
      merm.textContent = src;
      const klass = `diagram ${isCorrect ? "correct" : ""} ${isUserPick && !isCorrect ? "user-bad" : ""}`.trim();
      const tag = isCorrect && isUserPick ? "correct · your pick" : isCorrect ? "correct" : "your pick";
      grid.appendChild(el("div", { class: klass }, [
        el("div", { class: "diagram__label", text: `diagram ${label} · ${tag}` }),
        merm,
      ]));
    });
    body.push(grid);
  } else if (q.type === "open") {
    body.push(el("div", { class: "open-shown", text: `"${userVal || ''}"` }));
    if (r.feedback) {
      body.push(el("div", { class: "feedback" }, [
        el("div", { class: "feedback__head" }, [
          el("span", { class: "avatar", text: "CL" }),
          " LLM feedback",
        ]),
        el("p", { text: r.feedback }),
      ]));
    }
  }

  return el("article", { class: `file ${cls}` }, [
    el("div", { class: "file__head" }, [
      el("div", { class: "file__title", text: `Question ${i + 1}` }),
      el("div", { class: "file__score" }, [
        "score · ",
        el("b", { text: `${r.score} / 100` }),
      ]),
      el("div", { class: "file__verdict", text: verdict }),
    ]),
    el("div", { class: "file__body" }, body),
  ]);
}

function renderSidebarResults(results) {
  sidebarRoot.innerHTML = "";
  const correct = results.per_question.filter(r => r.correct).length;
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Score" }),
    el("div", { class: "side-score" }, [
      el("span", { class: "side-score__n", text: String(results.total_score) }),
      el("span", { class: "side-score__d", text: "/ 100" }),
    ]),
    el("div", { class: "progress-text", text: `${correct} of ${results.per_question.length} fully correct` }),
  ]));
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Per question" }),
    el("ul", { class: "sidelist" },
      results.per_question.map((r, i) => {
        const cls = scoreClass(r.score);
        const glyph = cls === "ok" ? "✓" : cls === "bad" ? "✗" : "~";
        return el("li", {}, [
          el("span", { class: `ic ${cls}`, text: glyph }),
          ` Q${i + 1}`,
          el("span", { class: "pts", text: String(r.score) }),
        ]);
      })
    ),
  ]));
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Visibility" }),
    el("div", { class: "side-text", text: "Private to you. Click publish to share as a PR comment." }),
  ]));
}

function renderReviewbarPublish() {
  reviewbar.className = "reviewbar is-publish";
  reviewbar.innerHTML = "";
  reviewbar.appendChild(el("div", { class: "reviewbar__msg" }, [
    el("b", { text: "Quiz private to you." }),
    " Publishing posts a scorecard comment on the PR.",
  ]));
  reviewbar.appendChild(el("div", { class: "reviewbar__spacer" }));
  reviewbar.appendChild(el("button", {
    class: "btn btn--secondary",
    type: "button",
    text: "Discard",
    onclick: () => { renderQuestions(); },
  }));
  reviewbar.appendChild(el("button", {
    class: "btn btn--primary",
    type: "button",
    text: "Publish to PR",
    onclick: publishResults,
  }));
}

function renderResults(results) {
  lastResults = results;
  questionsRoot.innerHTML = "";
  questionsRoot.appendChild(renderSummary(results));
  results.per_question.forEach((r, i) => {
    questionsRoot.appendChild(renderResultCard(quiz.questions[i], r, i));
  });
  renderSidebarResults(results);
  renderReviewbarPublish();
  // re-render any mermaid blocks in results
  window.mermaid.run({ querySelector: "#questions-root .mermaid" });
}

// replace the stub:
async function submitQuiz() {
  // disable button to prevent double-submit
  const btn = reviewbar.querySelector("button");
  btn.disabled = true;
  btn.textContent = "Submitting…";
  const payload = {
    version: "1",
    pr_number: quiz.pr_number,
    entries: quiz.questions.map(q => ({
      question_id: q.id,
      value: String(answers[q.id] ?? ""),
    })),
  };
  const resp = await fetch("/submit", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    btn.disabled = false;
    btn.textContent = "Submit quiz";
    alert(`Submit failed: ${resp.status}`);
    return;
  }
  const results = await resp.json();
  renderResults(results);
}

async function publishResults() {
  // implemented in Task 7
  console.log("publish pending", lastResults);
}
```

- [ ] **Step 6.4: Run the results test — expect PASS**

```bash
uv run pytest tests/server/test_ui_flow.py::test_submit_renders_results -v
```

Expected: PASS.

- [ ] **Step 6.5: Run full test file — expect PASS**

```bash
uv run pytest tests/server/test_ui_flow.py -v
```

Expected: 3 passes (initial load, mcq toggle, submit→results).

- [ ] **Step 6.6: Commit**

```bash
git add src/cognit/server/assets/quiz.js tests/server/test_ui_flow.py
git commit -m "feat(ui): results-state renderer (summary ring, per-question cards, sidebar swap)"
```

---

## Task 7: JS — published state renderer + `/publish` wiring

**Files:**
- Modify: `src/cognit/server/assets/quiz.js` (real `publishResults`, add `renderPublished`)
- Modify: `tests/server/test_ui_flow.py` (add published test, assert comment_url link)

**Context:** Clicking Publish POSTs `lastResults` to `/publish`, receives `{ok, total_score, comment_url}`. The UI prepends a success banner above the results, swaps the sidebar's Visibility block for a Timeline, and flips the reviewbar to its `is-published` state (green-tinted, with `Open on GitHub` linking to `comment_url`).

- [ ] **Step 7.1: Add published test**

Append to `tests/server/test_ui_flow.py`:

```python
def test_publish_renders_success_banner(live_server, page) -> None:
    base, posted = live_server
    page.goto(base, wait_until="networkidle")

    # answer + submit
    page.locator("#questions-root .file").nth(0).locator(".option").nth(1).click()
    page.locator("#questions-root .file").nth(1).locator(".diagram").first.click()
    page.locator("#questions-root .file").nth(2).locator("textarea").fill("answer")
    page.locator("#questions-root .file").nth(3).locator(".tf__cell").nth(1).click()
    page.locator("#reviewbar button").get_by_text("Submit", exact=False).click()
    page.wait_for_selector("#questions-root .summary", timeout=5000)

    # publish
    page.locator("#reviewbar button").get_by_text("Publish", exact=False).click()
    page.wait_for_selector("#questions-root .banner", timeout=5000)

    # banner contains a link to the comment_url returned by the fake post_comment
    banner = page.locator("#questions-root .banner")
    assert banner.is_visible()
    link = banner.locator("a")
    assert link.get_attribute("href") == "https://github.com/jonas/cognit/pull/142#issuecomment-9999"

    # reviewbar flipped to published state
    bar = page.locator("#reviewbar")
    assert "is-published" in (bar.get_attribute("class") or "")
    open_link = bar.locator("a").get_by_text("Open on GitHub", exact=False)
    assert open_link.is_visible()

    # markdown body was actually posted (FakeLLM doesn't render the markdown — the engine does)
    assert len(posted) == 1
    assert "/100" in posted[0] or "/ 100" in posted[0] or "score" in posted[0].lower()
```

- [ ] **Step 7.2: Run — expect FAIL**

```bash
uv run pytest tests/server/test_ui_flow.py::test_publish_renders_success_banner -v
```

Expected: FAIL — `publishResults` is a stub.

- [ ] **Step 7.3: Implement published renderer**

Append/replace in `src/cognit/server/assets/quiz.js`:

```javascript
// ── published-state renderers ───────────────────────────────────

function renderBanner(commentUrl) {
  return el("section", { class: "banner" }, [
    el("div", { class: "banner__icon", text: "✓" }),
    el("div", { class: "banner__body" }, [
      el("h2", { text: "Posted to PR · just now" }),
      el("p", { text: "Scorecard is live as a comment on the PR. Collaborators can see the score." }),
    ]),
    el("a", { class: "banner__cta", href: commentUrl, target: "_blank", rel: "noopener", text: "View comment ↗" }),
  ]);
}

function renderSidebarPublished(results) {
  // keep score block + per-question list, replace Visibility with Timeline
  renderSidebarResults(results);
  // remove the Visibility block (last side-block) and append Timeline
  const blocks = sidebarRoot.querySelectorAll(".side-block");
  if (blocks.length) blocks[blocks.length - 1].remove();
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Timeline" }),
    el("ul", { class: "timeline" }, [
      el("li", { class: "done", text: "Quiz generated" }),
      el("li", { class: "done", text: "Answered locally" }),
      el("li", { class: "done", text: "Graded" }),
      el("li", { class: "now", text: "Published to PR" }),
    ]),
  ]));
}

function renderReviewbarPublished(commentUrl) {
  reviewbar.className = "reviewbar is-published";
  reviewbar.innerHTML = "";
  reviewbar.appendChild(el("div", { class: "reviewbar__msg" }, [
    el("span", { class: "checkpill", text: "published" }),
    " Scorecard live on the PR.",
  ]));
  reviewbar.appendChild(el("div", { class: "reviewbar__spacer" }));
  reviewbar.appendChild(el("a", {
    class: "btn btn--external",
    href: commentUrl,
    target: "_blank",
    rel: "noopener",
    text: "Open on GitHub ↗",
  }));
}

function renderPublished(results, commentUrl) {
  // prepend banner to the existing results layout
  questionsRoot.insertBefore(renderBanner(commentUrl), questionsRoot.firstChild);
  renderSidebarPublished(results);
  renderReviewbarPublished(commentUrl);
}

// replace the stub:
async function publishResults() {
  if (!lastResults) return;
  const btn = reviewbar.querySelector("button.btn--primary");
  btn.disabled = true;
  btn.textContent = "Publishing…";
  const resp = await fetch("/publish", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(lastResults),
  });
  if (!resp.ok) {
    btn.disabled = false;
    btn.textContent = "Publish to PR";
    alert(`Publish failed: ${resp.status}`);
    return;
  }
  const data = await resp.json();
  renderPublished(lastResults, data.comment_url);
}
```

- [ ] **Step 7.4: Run the published test — expect PASS**

```bash
uv run pytest tests/server/test_ui_flow.py::test_publish_renders_success_banner -v
```

Expected: PASS.

- [ ] **Step 7.5: Run full suite**

```bash
uv run pytest -v
```

Expected: all pass (engine tests, server tests including 4 UI flow tests, smoke).

- [ ] **Step 7.6: Manual smoke against a real PR**

```bash
# in a checkout of a real PR branch:
cognit generate --pr "$(gh pr view --json url --jq .url)" --post
cognit take
```

Walk through: questions answer → submit → see results → click publish → success banner → click "View comment" → confirm it lands on the actual PR comment on GitHub.

- [ ] **Step 7.7: Commit**

```bash
git add src/cognit/server/assets/quiz.js tests/server/test_ui_flow.py
git commit -m "feat(ui): published-state renderer (success banner, timeline, external link to comment)"
```

---

## Task 8: Remove `mockups/` directory

**Files:**
- Delete: `mockups/` (entire directory)

**Context:** The mockups served their purpose — they're the source from which the production CSS was copied, and they're battle-tested as static prototypes. They're no longer the canonical UI; keeping them around invites drift and confusion. The Playwright integration tests in `tests/server/test_ui_flow.py` are the new canonical UI test.

- [ ] **Step 8.1: Remove mockups directory and the leftover battle-test script**

```bash
rm -rf mockups/
rm -rf /tmp/cognit-battle-test/  # optional cleanup of the throwaway test dir
```

- [ ] **Step 8.2: Confirm tests still pass (nothing references mockups/)**

```bash
uv run pytest -v
```

Expected: full suite passes.

- [ ] **Step 8.3: Commit**

```bash
git add -A
git commit -m "chore: remove mockups/ — production assets in src/cognit/server/assets/ are now canonical"
```

---

## Self-Review

Skimmed `UI-REDESIGN.md` section by section against the plan:

- **Scope: 3 surfaces (question / results / published)** → Tasks 5, 6, 7 ✓
- **Backend exception (`comment_url`)** → Task 1 ✓
- **Decision #1 (no files metaphor, "Question N" labels)** → Task 5 (renderQuestion produces `file__title` with "Question 1" string) ✓
- **Decision #2 (no rich posted-comment in published.html, link only)** → Task 7 (renderBanner produces `View comment ↗` link, no comment body re-render) ✓
- **Decision #3 (single ink score ring color)** → Task 6 (`--c: var(--fg)`) + Task 3 CSS note ✓
- **Decision #4 (topbar branded "cognit" not "GitHub")** → Task 2 (`.topbar__brand` with text "cognit", test asserts `>cognit<` and absence of "GitHub" branding) ✓
- **Decision #5 (sidebar kept)** → Task 2 shell has `#sidebar-root`, Tasks 5/6/7 populate it ✓
- **Component inventory CSS sections** → Task 3 structural test checks all section markers ✓
- **Mermaid restyle (ink-on-white)** → Task 3 (CSS overrides) + Task 5 (mermaid.initialize theme) ✓
- **JS state flow (3 render fns + wiring)** → Tasks 5/6/7 each add one renderer ✓
- **Responsive carry-over** → CSS in Task 3 includes the @media blocks from the mocks ✓
- **Risk: `comment_url` might not be available** → Addressed in Task 1 via `gh api ... --jq .html_url` ✓
- **Risk: mermaid render timing in results** → Addressed in Task 6 (`mermaid.run` called after `renderResults`) ✓
- **Risk: fake topbar feels performative** → Decision #4 reduces this; explicit re-eval after use is documented in spec, not in this plan ✓
- **Testing: extend the Playwright battle-test driver** → Tasks 4/5/6/7 build `tests/server/test_ui_flow.py` as the canonical UI test, replacing the throwaway `/tmp/cognit-battle-test/run.py` ✓

Placeholder scan: no TBDs, no "add error handling", no "similar to Task N". Every step has concrete code, an exact command, and an expected outcome.

Type consistency: `Callable[[str], str]` post_comment, `Quiz`/`Results` Pydantic models, `answers` JS object keyed by `question_id` — all consistent across Tasks 1, 5, 6, 7.

---

## Execution Handoff

Plan complete and saved to `UI-REDESIGN-PLAN.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each task is contained enough to ship on its own.
2. **Inline Execution** — I execute the plan in this session using executing-plans, batching with checkpoints. Same outcome, less parallelism, you see every step.

Which approach?
