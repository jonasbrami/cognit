// quizz front-end — github-native UI.
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
  await renderResults(results);
}

async function publishResults() {
  // implemented in Task 7
  console.log("publish pending", lastResults);
}

renderQuestions();
