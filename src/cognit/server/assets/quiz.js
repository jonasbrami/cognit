// cognit front-end — github-native UI.
// Contracts:
//   - reads window.QUIZ (shape in server/engine/models.py: Quiz)
//   - reads window.PR_URL
//   - POSTs to /submit, then optionally /publish
//   - mermaid is loaded via UMD; window.mermaid present before this script runs

window.mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
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

// Mutable: null on first paint when generation is still running (PHASE === "generating");
// set once /progress reports the finished quiz, then the normal UI renders.
let quiz = window.QUIZ;
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

// Split on `backticks` and render the spans as <code>. Safe — uses textContent.
function renderPrompt(text) {
  const parts = String(text).split(/(`[^`]+`)/g);
  return parts.map(p => {
    if (p.startsWith("`") && p.endsWith("`") && p.length >= 2) {
      return el("code", { text: p.slice(1, -1) });
    }
    return document.createTextNode(p);
  });
}

const TYPE_LABEL = {
  mcq: "Multiple choice",
  mermaid: "Diagram · pick a flow",
  open: "Open · LLM graded",
  tf: "True / False",
};

const LETTER = ["A", "B", "C", "D", "E", "F"];

// ── question renderers ──────────────────────────────────────────

function selectMCQOption(q, opts, idx) {
  answers[q.id] = q.options[idx];
  opts.forEach((o, j) => {
    o.classList.toggle("selected", j === idx);
    o.setAttribute("aria-checked", j === idx ? "true" : "false");
    o.setAttribute("tabindex", j === idx ? "0" : "-1");
  });
  updateReviewbarSubmit();
}

function renderMCQ(q) {
  const opts = q.options.map((opt, i) => {
    const node = el("div", {
      class: "option",
      role: "radio",
      "aria-checked": "false",
      tabindex: i === 0 ? "0" : "-1",
      onclick: () => selectMCQOption(q, opts, i),
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectMCQOption(q, opts, i);
        } else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
          e.preventDefault();
          const next = (i + 1) % opts.length;
          selectMCQOption(q, opts, next);
          opts[next].focus();
        } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
          e.preventDefault();
          const prev = (i - 1 + opts.length) % opts.length;
          selectMCQOption(q, opts, prev);
          opts[prev].focus();
        }
      },
    }, [
      el("div", { class: "option__radio" }),
      el("div", {}, [
        el("span", { class: "option__key", text: LETTER[i] }),
        el("span", { class: "option__text", text: opt }),
      ]),
    ]);
    return node;
  });
  const group = el("div", { role: "radiogroup", "aria-label": "Answer options" });
  opts.forEach(o => group.appendChild(o));
  return [group];
}

function renderMermaid(q) {
  const grid = el("div", { class: "diagrams", role: "radiogroup", "aria-label": "Answer options" });
  const cards = [];
  Object.entries(q.options).forEach(([label, src], i) => {
    const merm = el("div", { class: "mermaid" });
    merm.textContent = src;  // textContent only — never innerHTML (security)
    const card = el("div", {
      class: "diagram",
      role: "radio",
      "aria-checked": "false",
      tabindex: i === 0 ? "0" : "-1",
      onclick: () => {
        answers[q.id] = label;
        cards.forEach((c, j) => {
          c.classList.toggle("selected", j === i);
          c.setAttribute("aria-checked", j === i ? "true" : "false");
          c.setAttribute("tabindex", j === i ? "0" : "-1");
        });
        updateReviewbarSubmit();
      },
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          cards[i].click();
        } else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
          e.preventDefault();
          const next = (i + 1) % cards.length;
          cards[next].click();
          cards[next].focus();
        } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
          e.preventDefault();
          const prev = (i - 1 + cards.length) % cards.length;
          cards[prev].click();
          cards[prev].focus();
        }
      },
    }, [
      el("div", { class: "diagram__label", text: `diagram ${label}` }),
      merm,
    ]);
    cards.push(card);
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
  const wrap = el("div", { class: "tf", role: "radiogroup", "aria-label": "Answer options" });
  const cells = [];
  ["true", "false"].forEach((v, i) => {
    const cell = el("div", {
      class: "tf__cell",
      role: "radio",
      "aria-checked": "false",
      tabindex: i === 0 ? "0" : "-1",
      text: v.charAt(0).toUpperCase() + v.slice(1),
      onclick: () => {
        answers[q.id] = v;
        cells.forEach((c, j) => {
          c.classList.toggle("sel", j === i);
          c.setAttribute("aria-checked", j === i ? "true" : "false");
          c.setAttribute("tabindex", j === i ? "0" : "-1");
        });
        updateReviewbarSubmit();
      },
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          cells[i].click();
        } else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
          e.preventDefault();
          const next = (i + 1) % cells.length;
          cells[next].click();
          cells[next].focus();
        } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
          e.preventDefault();
          const prev = (i - 1 + cells.length) % cells.length;
          cells[prev].click();
          cells[prev].focus();
        }
      },
    });
    cells.push(cell);
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
      el("p", { class: "prompt" }, renderPrompt(q.prompt)),
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
  const btn = reviewbar.querySelector("button.btn--primary");
  if (!btn) return;
  const allAnswered = quiz.questions.every(q => {
    const v = answers[q.id];
    return v != null && v !== "";
  });
  btn.disabled = !allAnswered;
}

function renderReviewbarSubmit() {
  reviewbar.className = "reviewbar is-submit";
  reviewbar.innerHTML = "";
  reviewbar.appendChild(el("div", { class: "reviewbar__msg" }, [
    "Open question grades after submit.",
  ]));
  reviewbar.appendChild(el("div", { class: "reviewbar__spacer" }));
  const allAnswered = quiz.questions.every(q => {
    const v = answers[q.id];
    return v != null && v !== "";
  });
  const btn = el("button", {
    class: "btn btn--primary",
    type: "button",
    text: "Submit quiz",
    onclick: submitQuiz,
  });
  if (!allAnswered) btn.disabled = true;
  reviewbar.appendChild(btn);
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
    el("p", { class: "prompt" }, renderPrompt(q.prompt)),
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
    onclick: () => {
      // discard the submission AND any previously-entered answers (start fresh)
      Object.keys(answers).forEach(k => delete answers[k]);
      lastResults = null;
      renderQuestions();
    },
  }));
  reviewbar.appendChild(el("button", {
    class: "btn btn--primary",
    type: "button",
    text: "Publish to PR",
    onclick: publishResults,
  }));
}

async function renderResults(results) {
  lastResults = results;
  questionsRoot.innerHTML = "";
  questionsRoot.appendChild(renderSummary(results));
  results.per_question.forEach((r, i) => {
    questionsRoot.appendChild(renderResultCard(quiz.questions[i], r, i));
  });
  renderSidebarResults(results);
  renderReviewbarPublish();
  // re-render any mermaid blocks in results — await so tests can see finished SVGs
  await window.mermaid.run({ querySelector: "#questions-root .mermaid" });
}

function showGradingOverlay() {
  const feed = el("div", { class: "term term--overlay", id: "grade-feed" });
  const overlay = el("div", { class: "grading-overlay", role: "status", "aria-live": "polite" }, [
    el("div", { class: "grading-card" }, [
      el("div", { class: "gen__head" }, [
        el("span", { class: "gen__spinner", "aria-hidden": "true" }),
        el("h2", { text: "Grading your answers…" }),
      ]),
      feed,
    ]),
  ]);
  document.body.appendChild(overlay);
  return overlay;
}

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
  // Stream the open-question grading activity into an overlay while /submit runs.
  // The POST response is the authoritative result; the feed is just for show.
  const overlay = showGradingOverlay();
  const stopPolling = pollUntilStopped((ev) => appendTermLine(document.getElementById("grade-feed"), ev));
  let resp;
  try {
    resp = await fetch("/submit", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  } finally {
    stopPolling();
    overlay.remove();
  }
  if (!resp.ok) {
    btn.disabled = false;
    btn.textContent = "Submit quiz";
    alert(`Submit failed: ${resp.status}`);
    return;
  }
  const results = await resp.json();
  await renderResults(results);
}

// ── published-state renderers ───────────────────────────────────

function renderBanner(commentUrl) {
  return el("section", { class: "banner" }, [
    el("div", { class: "banner__icon", text: "✓" }),
    el("div", { class: "banner__body" }, [
      el("h2", { text: "Posted to PR · just now" }),
      el("p", { text: "Scorecard is live as a comment on the PR. Collaborators can see the score." }),
    ]),
    el("a", { class: "banner__cta", href: commentUrl, target: "_blank", rel: "noopener", text: "View comment" }),
  ]);
}

function renderSidebarPublished(results) {
  // keep score block + per-question list, replace Visibility with Timeline
  renderSidebarResults(results);
  // ASSUMES: renderSidebarResults's LAST side-block is the Visibility block.
  // If you add a new block to renderSidebarResults, append it BEFORE Visibility,
  // or this will silently rip out the wrong block.
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
    text: "Open on GitHub",
  }));
}

function renderPublished(results, commentUrl) {
  // prepend banner to the existing results layout
  questionsRoot.insertBefore(renderBanner(commentUrl), questionsRoot.firstChild);
  renderSidebarPublished(results);
  renderReviewbarPublished(commentUrl);
}

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

// ── live activity feed (generation + grading) ───────────────────
// On a cache miss the server starts before the quiz exists and streams Claude's
// activity into the broker; we poll /progress and replay from our own cursor, so
// refresh/reconnect just works. See server/streaming.py.

const POLL_MS = 500;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let feedCursor = 0;

const TOOL_LABELS = {
  submit_quiz: "Generating quiz",
  submit_grade: "Grading answer",
};

function appendTermLine(feed, ev) {
  if (!feed) return;
  let line = null;
  if (ev.kind === "step") {
    line = el("div", { class: "term__line term__step" }, [
      el("span", { class: "term__prompt", text: "›" }),
      el("span", { class: "term__label", text: TOOL_LABELS[ev.tool] || ev.tool }),
    ]);
  } else if (ev.kind === "tool_use") {
    const parts = [
      el("span", { class: "term__prompt", text: "·" }),
      el("span", { class: "term__dim", text: ev.name }),
    ];
    if (ev.detail) parts.push(el("span", { class: "term__text", text: " " + ev.detail }));
    line = el("div", { class: "term__line term__tool" }, parts);
  } else if (ev.kind === "thinking" && ev.text.trim() !== "") {
    line = el("div", { class: "term__line term__think" }, [
      el("span", { class: "term__prompt", text: "✳" }),
      el("span", { class: "term__dim", text: ev.text }),
    ]);
  } else if (ev.kind === "text" && ev.text.trim() !== "") {
    line = el("div", { class: "term__line" }, [
      el("span", { class: "term__prompt", text: " " }),
      el("span", { class: "term__text", text: ev.text }),
    ]);
  }
  if (!line) return;
  feed.appendChild(line);
  feed.scrollTop = feed.scrollHeight;
}

function renderGenerating() {
  questionsRoot.innerHTML = "";
  const feed = el("div", { class: "term", id: "term-feed" });
  questionsRoot.appendChild(el("section", { class: "gen" }, [
    el("div", { class: "gen__head" }, [
      el("span", { class: "gen__spinner", "aria-hidden": "true" }),
      el("h2", { text: "Generating your quiz…" }),
    ]),
    el("p", { class: "gen__sub", text: "Claude is reading the diff and writing questions. Runs locally; nothing is posted." }),
    feed,
  ]));
  sidebarRoot.innerHTML = "";
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Status" }),
    el("div", { class: "side-text", text: "Generating from the PR diff…" }),
  ]));
  reviewbar.className = "reviewbar";
  reviewbar.innerHTML = "";
}

function renderGenerationError(message) {
  questionsRoot.innerHTML = "";
  questionsRoot.appendChild(el("section", { class: "gen gen--error" }, [
    el("div", { class: "gen__head" }, [el("h2", { text: "Couldn't generate the quiz" })]),
    el("p", { class: "gen__sub", text: message || "Generation failed. See the terminal for details." }),
    el("p", { class: "gen__sub", text: "The quiz wasn't cached, so re-running cognit take will try again." }),
  ]));
  reviewbar.className = "reviewbar";
  reviewbar.innerHTML = "";
}

// Poll until generation reaches a terminal phase; returns the final snapshot.
async function pollGeneration(onEvent) {
  for (;;) {
    let data;
    try {
      data = await (await fetch(`/progress?cursor=${feedCursor}`)).json();
    } catch (e) {
      await sleep(POLL_MS);
      continue;
    }
    data.events.forEach(onEvent);
    feedCursor = data.next_cursor;
    if (data.phase !== "generating") return data;
    await sleep(POLL_MS);
  }
}

// Poll on an interval until stopped (used during grading, where phase stays "ready").
function pollUntilStopped(onEvent) {
  let stopped = false;
  (async () => {
    while (!stopped) {
      try {
        const data = await (await fetch(`/progress?cursor=${feedCursor}`)).json();
        data.events.forEach(onEvent);
        feedCursor = data.next_cursor;
      } catch (e) {
        /* transient — retry next tick */
      }
      if (stopped) break;
      await sleep(POLL_MS);
    }
  })();
  return () => { stopped = true; };
}

async function bootstrap() {
  if (window.PHASE === "ready" && quiz) {
    renderQuestions();
    return;
  }
  renderGenerating();
  const final = await pollGeneration((ev) => appendTermLine(document.getElementById("term-feed"), ev));
  if (final.phase === "ready" && final.quiz) {
    quiz = final.quiz;
    renderQuestions();
  } else {
    renderGenerationError(final.error);
  }
}

bootstrap();
