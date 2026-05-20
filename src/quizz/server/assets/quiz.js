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

async function submitQuiz() {
  // implemented in Task 6
  console.log("submit pending", answers);
}

async function publishResults() {
  // implemented in Task 7
  console.log("publish pending", lastResults);
}

renderQuestions();
