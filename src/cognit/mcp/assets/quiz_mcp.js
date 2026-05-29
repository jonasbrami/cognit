// cognit front-end — github-native UI, driven by the MCP host's /state.
// Data model (differs from the old server-rendered build):
//   - polls GET /state  → { quiz, answers, results }
//   - POSTs each answer to /answer as it's chosen (server is authoritative)
//   - POST /grade        → grade now (handler-owned; same path the agent's `grade` tool uses)
//   - POST /publish      → human-gated; posts the scorecard comment to the PR
// The terminal conversation can also steer (replace/add questions, grade); those land
// in /state and this page re-renders to match. Re-render happens only on a structural
// change so in-progress local input is never clobbered.

if (window.mermaid) {
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
}

const questionsRoot = document.getElementById("questions-root");
const sidebarRoot = document.getElementById("sidebar-root");
const reviewbar = document.getElementById("reviewbar");

// ── client state ────────────────────────────────────────────────
let quiz = null;            // current quiz (from /state)
const answers = {};         // { [question_id]: value } — local, mirrors /answer POSTs
let results = null;         // grading result (from /state or /grade)
let published = false;      // sticky once the scorecard is posted
let suppressResults = false; // local "Discard" → show answering even though results exist
let grading = false;        // grading in flight → pause polling re-renders
let renderedSig = null;     // signature of the rendered question structure
let view = null;            // waiting | answering | results | published

// ── small DOM helper ────────────────────────────────────────────
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

function quizSig(q) {
  return JSON.stringify(q.questions.map((x) => [
    x.id, x.type, x.prompt,
    x.type === "mcq" ? x.options : x.type === "mermaid" ? Object.keys(x.options) : null,
    x.anchor ? [x.anchor.path, x.anchor.start_line, x.anchor.end_line] : null,
  ]));
}

function isAnswered(q) {
  const v = answers[q.id];
  return v != null && v !== "";
}

// ── persist an answer to the server ─────────────────────────────
async function postAnswer(qid, value) {
  answers[qid] = value;
  try {
    const r = await fetch("/answer", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question_id: qid, value: String(value) }),
    });
    if (!r.ok) console.error("answer not saved:", r.status);
  } catch (e) {
    console.error("answer POST failed:", e);
  }
}

// Re-POST every non-empty local answer (idempotent) so the server has everything
// before grading — covers open answers that only POST on blur.
async function flushAnswers() {
  await Promise.all(quiz.questions.filter(isAnswered).map(q =>
    fetch("/answer", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question_id: q.id, value: String(answers[q.id]) }),
    }).catch(() => {})
  ));
}

// ── question renderers ──────────────────────────────────────────
function selectMCQOption(q, opts, idx) {
  postAnswer(q.id, q.options[idx]);
  opts.forEach((o, j) => {
    o.classList.toggle("selected", j === idx);
    o.setAttribute("aria-checked", j === idx ? "true" : "false");
    o.setAttribute("tabindex", j === idx ? "0" : "-1");
  });
  updateReviewbarSubmit();
}

function renderMCQ(q) {
  const opts = q.options.map((opt, i) => el("div", {
    class: "option" + (answers[q.id] === opt ? " selected" : ""),
    role: "radio",
    "aria-checked": answers[q.id] === opt ? "true" : "false",
    tabindex: i === 0 ? "0" : "-1",
    onclick: () => selectMCQOption(q, opts, i),
    onkeydown: (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectMCQOption(q, opts, i); }
      else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault(); const n = (i + 1) % opts.length; selectMCQOption(q, opts, n); opts[n].focus();
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault(); const p = (i - 1 + opts.length) % opts.length; selectMCQOption(q, opts, p); opts[p].focus();
      }
    },
  }, [
    el("div", { class: "option__radio" }),
    el("div", {}, [
      el("span", { class: "option__key", text: LETTER[i] }),
      el("span", { class: "option__text", text: opt }),
    ]),
  ]));
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
      class: "diagram" + (answers[q.id] === label ? " selected" : ""),
      role: "radio",
      "aria-checked": answers[q.id] === label ? "true" : "false",
      tabindex: i === 0 ? "0" : "-1",
      onclick: () => {
        postAnswer(q.id, label);
        cards.forEach((c, j) => {
          c.classList.toggle("selected", j === i);
          c.setAttribute("aria-checked", j === i ? "true" : "false");
          c.setAttribute("tabindex", j === i ? "0" : "-1");
        });
        updateReviewbarSubmit();
      },
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cards[i].click(); }
        else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
          e.preventDefault(); const n = (i + 1) % cards.length; cards[n].click(); cards[n].focus();
        } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
          e.preventDefault(); const p = (i - 1 + cards.length) % cards.length; cards[p].click(); cards[p].focus();
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
    onblur: (e) => postAnswer(q.id, e.target.value),
  });
  ta.value = answers[q.id] || "";
  return [ta];
}

function renderTF(q) {
  const wrap = el("div", { class: "tf", role: "radiogroup", "aria-label": "Answer options" });
  const cells = [];
  ["true", "false"].forEach((v, i) => {
    const cell = el("div", {
      class: "tf__cell" + (answers[q.id] === v ? " sel" : ""),
      role: "radio",
      "aria-checked": answers[q.id] === v ? "true" : "false",
      tabindex: i === 0 ? "0" : "-1",
      text: v.charAt(0).toUpperCase() + v.slice(1),
      onclick: () => {
        postAnswer(q.id, v);
        cells.forEach((c, j) => {
          c.classList.toggle("sel", j === i);
          c.setAttribute("aria-checked", j === i ? "true" : "false");
          c.setAttribute("tabindex", j === i ? "0" : "-1");
        });
        updateReviewbarSubmit();
      },
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cells[i].click(); }
        else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
          e.preventDefault(); const n = (i + 1) % cells.length; cells[n].click(); cells[n].focus();
        } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
          e.preventDefault(); const p = (i - 1 + cells.length) % cells.length; cells[p].click(); cells[p].focus();
        }
      },
    });
    cells.push(cell);
    wrap.appendChild(cell);
  });
  return [wrap];
}

// ── inline code context (anchors) ───────────────────────────────
// A collapsible panel under a question that shows the anchored diff hunk. The hunk
// is fetched from GET /diff on first expand and rendered DOM-built (textContent only,
// never innerHTML) so agent/repo-supplied diff text can't inject markup.
const _diffCache = {};  // path -> diff text (one fetch per file per page)

// Split a unified-diff file section into the lines before the first hunk (file header)
// and the hunks. Each hunk carries its new-side line span so we can scope to an anchor.
function parseDiff(text) {
  const hunks = [];
  let cur = null;
  for (const line of String(text).split("\n")) {
    if (line.startsWith("@@")) {
      const m = /@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(line);
      const startNew = m ? parseInt(m[1], 10) : 1;
      const countNew = m && m[2] != null ? parseInt(m[2], 10) : 1;
      cur = { startNew, endNew: startNew + Math.max(countNew, 1) - 1, lines: [line] };
      hunks.push(cur);
    } else if (cur) {
      cur.lines.push(line);
    }
  }
  return hunks;
}

function diffLineNode(line, lineNo, anchor) {
  let cls = "diff-line";
  if (line.startsWith("@@")) cls += " hunk";
  else if (/^(diff |index |\+\+\+|---)/.test(line)) cls += " fmeta";
  else if (line.startsWith("+")) cls += " add";
  else if (line.startsWith("-")) cls += " del";
  if (anchor && lineNo != null && lineNo >= anchor.start_line && lineNo <= anchor.end_line) {
    cls += " anchor-hit";
  }
  return el("div", { class: cls, text: line === "" ? " " : line });
}

// Render the diff, scoped to the hunk(s) the anchor points at (new-side line range).
// Falls back to all hunks if the anchor overlaps none (e.g. it points at context the
// diff doesn't touch). Anchored new-side lines are highlighted. textContent only.
function renderDiff(text, anchor) {
  const hunks = parseDiff(text);
  let shown = hunks;
  if (anchor && hunks.length) {
    const overlap = hunks.filter(
      (h) => h.startNew <= anchor.end_line && h.endNew >= anchor.start_line
    );
    if (overlap.length) shown = overlap;
  }
  const pre = el("pre", { class: "diff" });
  if (!shown.length) {  // no @@ hunks (binary/empty section) — show raw, no numbering
    String(text).split("\n").forEach((line) => pre.appendChild(diffLineNode(line, null, null)));
    return pre;
  }
  shown.forEach((h) => {
    let newLine = h.startNew;
    h.lines.forEach((line) => {
      if (line.startsWith("@@")) {
        pre.appendChild(diffLineNode(line, null, anchor));
      } else if (line.startsWith("-")) {
        pre.appendChild(diffLineNode(line, null, anchor));  // removed: no new-side number
      } else {
        pre.appendChild(diffLineNode(line, newLine, anchor));  // context / added
        newLine++;
      }
    });
  });
  return pre;
}

async function loadHunk(path, anchor, body) {
  body.textContent = "Loading…";
  let text;
  try {
    if (!(path in _diffCache)) {
      _diffCache[path] = await (await fetch("/diff?path=" + encodeURIComponent(path))).text();
    }
    text = _diffCache[path];
  } catch (e) {
    console.warn("diff fetch failed for", path, e);
    body.textContent = "Could not load the diff for this file.";
    return;
  }
  body.textContent = "";
  if (/^No changed file matches/.test(text)) {
    // path isn't in the PR diff (e.g. renamed, or a filtered binary/minified file)
    body.appendChild(el("div", { class: "codepanel__note", text: "Not part of the PR diff." }));
    return;
  }
  body.appendChild(renderDiff(text, anchor));
}

function renderAnchor(q) {
  const a = q.anchor;
  if (!a || !a.path) return null;
  const range = a.start_line === a.end_line ? `${a.start_line}` : `${a.start_line}–${a.end_line}`;
  const body = el("div", { class: "codepanel__body" });
  const details = el("details", { class: "codepanel" }, [
    el("summary", { class: "codepanel__summary" }, [
      el("span", { class: "codepanel__icon", "aria-hidden": "true", text: "▸" }),
      el("span", { class: "codepanel__file", text: a.path }),
      el("span", { class: "codepanel__lines", text: `:${range}` }),
    ]),
    body,
  ]);
  let loaded = false;
  details.addEventListener("toggle", () => {
    if (details.open && !loaded) { loaded = true; loadHunk(a.path, a, body); }
  });
  return details;
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
      renderAnchor(q),  // null when no anchor — el() skips null children
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
      quiz.questions.map(() => el("span", { class: "progress__dot" }))),
    el("div", { class: "progress-text", text: `0 of ${quiz.questions.length} answered` }),
  ]));
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Questions" }),
    el("ul", { class: "sidelist" },
      quiz.questions.map((q, i) => el("li", {}, [
        el("span", { class: "check empty", text: "○" }),
        ` Q${i + 1} · ${TYPE_LABEL[q.type].split(" ")[0].toLowerCase()}`,
      ]))),
  ]));
  renderCoverageBlock();  // async: appends "Diff coverage" once the file list is fetched
}

// ── diff coverage map ───────────────────────────────────────────
// Sidebar list of the PR's changed files, marked covered when some question is
// anchored to them. Display only (the "ask host to cover this" steer is Track B).
let changedFiles = null;  // string[] — fetched once per page (null = not yet fetched)

async function ensureChangedFiles() {
  if (changedFiles !== null) return changedFiles;
  try {
    const r = await fetch("/changed-files");
    changedFiles = r.ok ? ((await r.json()).files || []) : [];
  } catch (e) {
    console.warn("changed-files fetch failed", e);
    changedFiles = [];
  }
  return changedFiles;
}

// Mirror of the server's do_file_diff path matching (exact, repo-relative suffix,
// or basename) so a covered marker lines up with what /diff would actually serve.
function fileMatchesAnchor(file, anchorPath) {
  if (!anchorPath) return false;
  if (file === anchorPath || file.endsWith("/" + anchorPath) || anchorPath.endsWith("/" + file)) {
    return true;
  }
  return file.split("/").pop() === anchorPath.split("/").pop();
}

async function renderCoverageBlock() {
  const files = await ensureChangedFiles();
  if (!files.length || !quiz) return;  // no diff info, or quiz cleared while awaiting
  sidebarRoot.querySelector(".side-block--coverage")?.remove();  // idempotent re-render
  const anchorPaths = quiz.questions.map((q) => q.anchor && q.anchor.path).filter(Boolean);
  const rows = files.map((f) => ({ path: f, covered: anchorPaths.some((ap) => fileMatchesAnchor(f, ap)) }));
  const coveredCount = rows.filter((r) => r.covered).length;
  sidebarRoot.appendChild(el("div", { class: "side-block side-block--coverage" }, [
    el("div", { class: "side-title", text: "Diff coverage" }),
    el("div", { class: "progress-text", text: `${coveredCount} of ${rows.length} files probed` }),
    el("ul", { class: "sidelist coverage" },
      rows.map((r) => el("li", { class: r.covered ? "covered" : "uncovered", title: r.path }, [
        el("span", { class: `ic ${r.covered ? "ok" : "empty"}`, text: r.covered ? "✓" : "○" }),
        el("span", { class: "coverage__file", text: ` ${r.path.split("/").pop()}` }),
      ]))),
  ]));
}

function updateSidebarProgress() {
  if (!quiz) return;
  const total = quiz.questions.length;
  const done = quiz.questions.filter(isAnswered).length;
  sidebarRoot.querySelectorAll(".progress__dot").forEach((dot, i) => {
    dot.classList.toggle("done", i < done);
  });
  const txt = sidebarRoot.querySelector(".progress-text");
  if (txt) txt.textContent = `${done} of ${total} answered`;
  sidebarRoot.querySelectorAll(".sidelist .check").forEach((c, i) => {
    if (isAnswered(quiz.questions[i])) { c.textContent = "✓"; c.classList.remove("empty"); }
    else { c.textContent = "○"; c.classList.add("empty"); }
  });
}

// ── reviewbar — submit state ────────────────────────────────────
function updateReviewbarSubmit() {
  updateSidebarProgress();
  const btn = reviewbar.querySelector("button.btn--primary");
  if (!btn) return;
  btn.disabled = !quiz.questions.every(isAnswered);
}

function renderReviewbarSubmit() {
  reviewbar.className = "reviewbar is-submit";
  reviewbar.innerHTML = "";
  const hasOpen = quiz.questions.some(q => q.type === "open");
  reviewbar.appendChild(el("div", { class: "reviewbar__msg" }, [
    hasOpen ? "Open question grades after submit." : "Answers stay private until you submit.",
  ]));
  reviewbar.appendChild(el("div", { class: "reviewbar__spacer" }));
  const btn = el("button", {
    class: "btn btn--primary", type: "button", text: "Submit quiz", onclick: submitQuiz,
  });
  if (!quiz.questions.every(isAnswered)) btn.disabled = true;
  reviewbar.appendChild(btn);
}

// ── answering view ──────────────────────────────────────────────
function renderQuestions() {
  questionsRoot.innerHTML = "";
  quiz.questions.forEach((q, i) => questionsRoot.appendChild(renderQuestion(q, i)));
  renderSidebar();
  renderReviewbarSubmit();
  updateSidebarProgress();
  if (window.mermaid) {
    window.mermaid.run({ querySelector: "#questions-root .mermaid" }).catch(e => console.error("mermaid.run failed:", e));
  }
}

// ── results view ────────────────────────────────────────────────
function scoreClass(score) {
  if (score >= 90) return "ok";
  if (score >= 60) return "mid";
  return "bad";
}

function resultsById(res) {
  const m = {};
  res.per_question.forEach(r => { m[r.question_id] = r; });
  return m;
}

function renderSummary(res) {
  const total = res.total_score;
  const pips = res.per_question.map(r => {
    const cls = scoreClass(r.score);
    const glyph = cls === "ok" ? "✓" : cls === "bad" ? "✗" : "~";
    return el("span", { class: `pip pip--${cls}`, text: glyph });
  });
  return el("section", { class: "summary" }, [
    el("div", { class: "summary__ring", style: `--val: ${total}; --c: var(--fg);` }, [
      el("div", { class: "summary__num", text: String(total) }, [el("small", { text: "/ 100" })]),
    ]),
    el("div", { class: "summary__body" }, [
      el("h2", { text: `Scored locally · ${res.per_question.filter(r => r.correct).length} of ${res.per_question.length} right` }),
      el("p", { text: "Below: per-question breakdown. The open answer is graded by the LLM." }),
      el("div", { class: "summary__pips" }, pips),
    ]),
  ]);
}

function renderResultCard(q, r, i) {
  const cls = scoreClass(r.score);
  const verdict = cls === "ok" ? "correct" : cls === "bad" ? "incorrect" : "partial";
  const body = [el("p", { class: "prompt" }, renderPrompt(q.prompt)), renderAnchor(q)];
  const userVal = answers[q.id];
  if (q.type === "mcq" || q.type === "tf") {
    const correctAnswer = q.type === "tf" ? String(q.answer) : q.answer;
    body.push(el("div", { class: `ans-row user-${cls === "ok" ? "ok" : "bad"}` }, [
      el("div", { class: "ans-row__icon", text: cls === "ok" ? "✓" : "✗" }),
      el("div", { class: "ans-row__text", text: String(userVal ?? "—") }),
      el("div", { class: "ans-row__tag", text: cls === "ok" ? "correct" : "your pick" }),
    ]));
    if (cls !== "ok") {
      body.push(el("div", { class: "ans-row correct" }, [
        el("div", { class: "ans-row__icon", text: "✓" }),
        el("div", { class: "ans-row__text", text: String(correctAnswer) }),
        el("div", { class: "ans-row__tag", text: "correct answer" }),
      ]));
    }
  } else if (q.type === "mermaid") {
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
        el("div", { class: "feedback__head" }, [el("span", { class: "avatar", text: "CL" }), " LLM feedback"]),
        el("p", { text: r.feedback }),
      ]));
    }
  }
  if (q.explanation) {
    body.push(el("div", { class: "feedback" }, [
      el("div", { class: "feedback__head" }, ["Why"]),
      el("p", { text: q.explanation }),
    ]));
  }
  return el("article", { class: `file ${cls}` }, [
    el("div", { class: "file__head" }, [
      el("div", { class: "file__title", text: `Question ${i + 1}` }),
      el("div", { class: "file__score" }, ["score · ", el("b", { text: `${r.score} / 100` })]),
      el("div", { class: "file__verdict", text: verdict }),
    ]),
    el("div", { class: "file__body" }, body),
  ]);
}

function renderSidebarResults(res) {
  sidebarRoot.innerHTML = "";
  const byId = resultsById(res);
  const correct = res.per_question.filter(r => r.correct).length;
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Score" }),
    el("div", { class: "side-score" }, [
      el("span", { class: "side-score__n", text: String(res.total_score) }),
      el("span", { class: "side-score__d", text: "/ 100" }),
    ]),
    el("div", { class: "progress-text", text: `${correct} of ${res.per_question.length} fully correct` }),
  ]));
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Per question" }),
    el("ul", { class: "sidelist" },
      quiz.questions.map((q, i) => {
        const r = byId[q.id];
        if (!r) return el("li", {}, [el("span", { class: "check empty", text: "○" }), ` Q${i + 1}`]);
        const cls = scoreClass(r.score);
        const glyph = cls === "ok" ? "✓" : cls === "bad" ? "✗" : "~";
        return el("li", {}, [
          el("span", { class: `ic ${cls}`, text: glyph }),
          ` Q${i + 1}`,
          el("span", { class: "pts", text: String(r.score) }),
        ]);
      })),
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
    class: "btn btn--secondary", type: "button", text: "Discard",
    onclick: () => {
      suppressResults = true;
      Object.keys(answers).forEach(k => delete answers[k]);
      renderQuestions();
      view = "answering";
    },
  }));
  reviewbar.appendChild(el("button", {
    class: "btn btn--primary", type: "button", text: "Publish to PR", onclick: publishResults,
  }));
}

async function renderResults(res) {
  results = res;
  questionsRoot.innerHTML = "";
  questionsRoot.appendChild(renderSummary(res));
  const byId = resultsById(res);
  quiz.questions.forEach((q, i) => {
    const r = byId[q.id];
    if (r) questionsRoot.appendChild(renderResultCard(q, r, i));
  });
  renderSidebarResults(res);
  renderReviewbarPublish();
  if (window.mermaid) {
    await window.mermaid.run({ querySelector: "#questions-root .mermaid" }).catch(e => console.error("mermaid.run failed:", e));
  }
}

// ── grading overlay (spinner only; the terminal shows the live activity) ──
function showGradingOverlay() {
  const overlay = el("div", { class: "grading-overlay", role: "status", "aria-live": "polite" }, [
    el("div", { class: "grading-card" }, [
      el("div", { class: "gen__head" }, [
        el("span", { class: "gen__spinner", "aria-hidden": "true" }),
        el("h2", { text: "Grading your answers…" }),
      ]),
      el("p", { class: "gen__sub", text: "Scoring locally. The open answer is graded by the LLM." }),
    ]),
  ]);
  document.body.appendChild(overlay);
  return overlay;
}

async function submitQuiz() {
  const btn = reviewbar.querySelector("button.btn--primary");
  if (btn) { btn.disabled = true; btn.textContent = "Submitting…"; }
  suppressResults = false;
  grading = true;
  const overlay = showGradingOverlay();
  let resp;
  try {
    await flushAnswers();
    resp = await fetch("/grade", { method: "POST" });
  } catch (e) {
    overlay.remove(); grading = false;
    if (btn) { btn.disabled = false; btn.textContent = "Submit quiz"; }
    alert("Grading failed: network error");
    return;
  }
  overlay.remove();
  grading = false;
  if (!resp.ok) {
    if (btn) { btn.disabled = false; btn.textContent = "Submit quiz"; }
    alert(`Grading failed: ${resp.status}`);
    return;
  }
  const res = await resp.json();
  await renderResults(res);
  view = "results";
}

// ── published view ──────────────────────────────────────────────
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

function renderSidebarPublished() {
  renderSidebarResults(results);
  // ASSUMES renderSidebarResults's LAST side-block is Visibility — replace it with Timeline.
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
    class: "btn btn--external", href: commentUrl, target: "_blank", rel: "noopener", text: "Open on GitHub",
  }));
}

function renderPublished(commentUrl) {
  questionsRoot.insertBefore(renderBanner(commentUrl), questionsRoot.firstChild);
  renderSidebarPublished();
  renderReviewbarPublished(commentUrl);
}

async function publishResults() {
  const btn = reviewbar.querySelector("button.btn--primary");
  if (btn) { btn.disabled = true; btn.textContent = "Publishing…"; }
  let resp;
  try {
    resp = await fetch("/publish", { method: "POST" });
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "Publish to PR"; }
    alert("Publish failed: network error");
    return;
  }
  if (!resp.ok) {
    if (btn) { btn.disabled = false; btn.textContent = "Publish to PR"; }
    let detail = `HTTP ${resp.status}`;
    try { const j = await resp.json(); if (j && j.error) detail = j.error; } catch (e) { /* non-JSON */ }
    alert(`Publish failed: ${detail}`);
    return;
  }
  const data = await resp.json();
  published = true;
  view = "published";
  renderPublished(data.comment_url);
}

// ── waiting view (no quiz yet — the terminal shows generation activity) ──
function renderWaiting() {
  questionsRoot.innerHTML = "";
  questionsRoot.appendChild(el("section", { class: "gen" }, [
    el("div", { class: "gen__head" }, [
      el("span", { class: "gen__spinner", "aria-hidden": "true" }),
      el("h2", { text: "Generating your quiz…" }),
    ]),
    el("p", { class: "gen__sub", text: "cognit is reading the diff and writing questions in your terminal. This page updates automatically." }),
  ]));
  sidebarRoot.innerHTML = "";
  sidebarRoot.appendChild(el("div", { class: "side-block" }, [
    el("div", { class: "side-title", text: "Status" }),
    el("div", { class: "side-text", text: "Generating from the PR diff…" }),
  ]));
  reviewbar.className = "reviewbar";
  reviewbar.innerHTML = "";
}

// ── poll loop / state machine ───────────────────────────────────
let ticking = false;
async function tick() {
  if (grading || ticking) return;
  ticking = true;
  try {
    let state;
    try { state = await (await fetch("/state")).json(); }
    catch (e) { return; }

    if (!state.quiz) {
      if (view !== "waiting") { renderWaiting(); view = "waiting"; }
      renderedSig = null; quiz = null;
      return;
    }
    quiz = state.quiz;
    // Seed from server only for answers we don't already have locally (resume /
    // multi-tab) — never clobber an in-progress local selection.
    for (const [k, v] of Object.entries(state.answers || {})) {
      if (!(k in answers)) answers[k] = v;
    }
    if (state.results == null) { suppressResults = false; published = false; }

    if (published) return;  // published view is sticky until results clear

    if (state.results && !suppressResults) {
      if (view !== "results") { await renderResults(state.results); view = "results"; }
      return;
    }

    const newSig = quizSig(quiz);
    if (view !== "answering" || newSig !== renderedSig) {
      renderedSig = newSig;
      renderQuestions();
      view = "answering";
    } else {
      updateSidebarProgress();
    }
  } finally {
    ticking = false;
  }
}

setInterval(tick, 1000);
tick();
