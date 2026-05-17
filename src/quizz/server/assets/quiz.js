// quizz front-end — editorial diagnostic UI.
// Contracts (must hold):
//   - reads window.QUIZ (shape documented in server/app.py)
//   - POSTs to /submit, then /publish (opt-in)
//   - mermaid is loaded via UMD script tag in index.html and attached to window.mermaid
//   - diagrams must live in elements with class="mermaid" and textContent (not innerHTML) set

window.mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  fontFamily:
    '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace',
  themeVariables: {
    background: "transparent",
    primaryColor: "#f5efe4",
    primaryBorderColor: "#1f3a5f",
    primaryTextColor: "#1a1612",
    lineColor: "#1f3a5f",
    secondaryColor: "#e3ecf2",
    tertiaryColor: "#e3ecf2",
    fontSize: "13px",
  },
});

const quiz = window.QUIZ;
const root = document.getElementById("quiz");
const submitBtn = document.getElementById("submit");
const submitLabel = submitBtn.querySelector(".commit-bar__submit-label");
const resultEl = document.getElementById("result");

// Cached so the Publish button can re-send without re-grading.
let lastResults = null;

// Small DOM helper. `text` sets textContent; `html` is intentionally absent.
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "for") node.htmlFor = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null || child === false) continue;
    if (typeof child === "string") node.appendChild(document.createTextNode(child));
    else node.appendChild(child);
  }
  return node;
}

const TYPE_LABEL = {
  mcq: "Multiple choice",
  mermaid: "Architecture",
  open: "Free response",
  tf: "True or false",
};

// English-ordinal numerals for the rail. Past 12 we fall back to digits.
const ORDINALS = [
  "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii",
];
function numeralFor(index) {
  return ORDINALS[index] || String(index + 1);
}

function buildRail(index, type) {
  return el("div", { class: "question__rail" }, [
    el("span", { class: "question__num", text: numeralFor(index) }),
    el("span", { class: "question__type", text: TYPE_LABEL[type] || type }),
  ]);
}

// Render a prompt that may contain backtick-fenced inline code (`foo`).
// We render the surrounding text as a single <p>, swapping `…` for <code>.
function renderPrompt(text) {
  const p = el("p", { class: "question__prompt" });
  const parts = text.split(/(`[^`]+`)/g);
  for (const part of parts) {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      p.appendChild(el("code", { text: part.slice(1, -1) }));
    } else if (part) {
      p.appendChild(document.createTextNode(part));
    }
  }
  return p;
}

function makeChoice({ name, value, label, letter, extra, mermaidSrc }) {
  // Each .choice is itself a <label> so the entire row is clickable.
  const input = document.createElement("input");
  input.type = "radio";
  input.name = name;
  input.value = value;

  const mark = el("span", { class: "choice__mark", "aria-hidden": "true" });

  const labelEl = el("span", { class: "choice__label" });
  if (letter) {
    labelEl.appendChild(el("span", { class: "choice__letter", text: letter }));
  }
  if (label != null) {
    labelEl.appendChild(document.createTextNode(label));
  }
  if (extra) labelEl.appendChild(extra);

  const row = el(
    "label",
    { class: mermaidSrc ? "choice choice--mermaid" : "choice" },
    []
  );
  if (mermaidSrc) {
    const header = el("span", { class: "choice__header" }, [
      mark,
      el("span", { class: "choice__letter", text: letter || "" }),
      el("span", { text: "diagram" }),
    ]);
    const diagram = el("div", { class: "mermaid" });
    diagram.textContent = mermaidSrc;
    row.appendChild(input);
    row.appendChild(header);
    row.appendChild(diagram);
  } else {
    row.appendChild(input);
    row.appendChild(mark);
    row.appendChild(labelEl);
  }
  return row;
}

function renderMCQ(q, index) {
  const list = el("ul", { class: "choices choices--mcq" });
  const letters = ["A", "B", "C", "D", "E", "F", "G", "H"];
  q.options.forEach((opt, i) => {
    list.appendChild(
      el("li", {}, [
        makeChoice({
          name: q.id,
          value: opt,
          label: opt,
          letter: letters[i] || String(i + 1),
        }),
      ])
    );
  });

  return el(
    "article",
    { class: "question question--mcq", "data-qid": q.id, style: `animation-delay:${index * 80}ms` },
    [buildRail(index, q.type), renderPrompt(q.prompt), list]
  );
}

function renderMermaid(q, index) {
  const list = el("ul", { class: "choices choices--mermaid" });
  for (const [label, src] of Object.entries(q.options)) {
    list.appendChild(
      el("li", {}, [
        makeChoice({
          name: q.id,
          value: label,
          letter: label,
          mermaidSrc: src,
        }),
      ])
    );
  }
  return el(
    "article",
    { class: "question question--mermaid", "data-qid": q.id, style: `animation-delay:${index * 80}ms` },
    [buildRail(index, q.type), renderPrompt(q.prompt), list]
  );
}

function renderOpen(q, index) {
  const ta = document.createElement("textarea");
  ta.name = q.id;
  ta.rows = 6;
  ta.placeholder = "Write as if explaining to the engineer who'll inherit this code…";
  ta.setAttribute("spellcheck", "true");
  const field = el("div", { class: "open-field" }, [
    ta,
    el("span", {
      class: "open-field__caption",
      text: "graded by an LLM against the rubric — a few sentences is plenty",
    }),
  ]);
  return el(
    "article",
    { class: "question question--open", "data-qid": q.id, style: `animation-delay:${index * 80}ms` },
    [buildRail(index, q.type), renderPrompt(q.prompt), field]
  );
}

function renderTF(q, index) {
  const list = el("ul", { class: "choices choices--tf" }, [
    el("li", {}, [makeChoice({ name: q.id, value: "true", label: "True" })]),
    el("li", {}, [makeChoice({ name: q.id, value: "false", label: "False" })]),
  ]);
  return el(
    "article",
    { class: "question question--tf", "data-qid": q.id, style: `animation-delay:${index * 80}ms` },
    [buildRail(index, q.type), renderPrompt(q.prompt), list]
  );
}

async function render() {
  quiz.questions.forEach((q, i) => {
    let node;
    if (q.type === "mcq") node = renderMCQ(q, i);
    else if (q.type === "mermaid") node = renderMermaid(q, i);
    else if (q.type === "open") node = renderOpen(q, i);
    else if (q.type === "tf") node = renderTF(q, i);
    if (node) root.appendChild(node);
  });
  try {
    await window.mermaid.run({ querySelector: ".mermaid" });
  } catch (e) {
    console.error("mermaid.run() failed", e);
  }
}

// ── results ───────────────────────────────────────────────────────────────

function scoreCaption(total) {
  if (total >= 95) return "Calibrated. Your mental model matches the code.";
  if (total >= 80) return "Close. A small gap — worth a re-read of the rough edges below.";
  if (total >= 60) return "Useful. The feedback below is where the medicine is.";
  if (total >= 30) return "Honest. This is the gap the diagnostic is built to surface.";
  return "Brave of you to look. Now you know what to read again.";
}

function buildResultItem(r) {
  const ok = !!r.correct;
  const glyph = ok ? "¶" : "§"; // pilcrow / section sign
  const item = el(
    "li",
    { class: `result-item ${ok ? "result-item--ok" : "result-item--bad"}` },
    [
      el("span", { class: "result-item__glyph", text: glyph, "aria-hidden": "true" }),
      el("span", { class: "result-item__id", text: r.question_id }),
      el("span", { class: "result-item__score", text: `${r.score}%` }),
    ]
  );
  if (r.feedback) {
    item.appendChild(el("p", { class: "result-item__feedback", text: r.feedback }));
  }
  return item;
}

function renderResults(results) {
  // Replace contents wholesale on every (re)submit.
  resultEl.innerHTML = "";

  const card = el("div", { class: "result-card" });

  const score = el("div", { class: "result-score" }, [
    el("span", { class: "result-score__num", text: String(results.total_score) }),
    el("span", { class: "result-score__pct", text: "%" }),
  ]);

  const caption = el("div", { class: "result-caption" }, [
    el("span", { class: "result-caption__kicker", text: "Result" }),
    document.createTextNode(scoreCaption(results.total_score)),
  ]);

  card.appendChild(el("div", { class: "result-header" }, [score, caption]));

  const list = el("ul", { class: "result-list" });
  for (const r of results.per_question) list.appendChild(buildResultItem(r));
  card.appendChild(list);

  // ── publish block ─────────────────────────────────────────────────────
  const publishBtn = el("button", {
    id: "publish",
    class: "publish",
    type: "button",
  }, [
    el("span", { text: "Publish to PR" }),
    el("span", { class: "publish__arrow", "aria-hidden": "true", text: "↗" }),
  ]);
  const publishStatus = el("span", { id: "publish-status", class: "publish-status" });

  const publishBlock = el("div", { class: "publish-block" }, [
    el("div", { class: "publish-block__copy" }, [
      el("div", { class: "publish-block__kicker", text: "Opt-in" }),
      el("h3", { class: "publish-block__title", text: "Show your work?" }),
      el(
        "p",
        {
          class: "publish-block__body",
          text:
            "Posting your score back to the pull request takes a little courage. Skip it if you'd rather keep this private — the diagnostic still did its job.",
        }
      ),
    ]),
    publishBtn,
    publishStatus,
  ]);
  card.appendChild(publishBlock);

  resultEl.appendChild(card);

  publishBtn.addEventListener("click", async () => {
    publishBtn.disabled = true;
    publishStatus.className = "publish-status";
    publishStatus.replaceChildren(
      el("span", { class: "publish-status__spin", "aria-hidden": "true" }),
      el("span", { text: "Posting to GitHub…" })
    );
    try {
      const r = await fetch("/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastResults),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      publishStatus.className = "publish-status publish-status--ok";
      publishStatus.replaceChildren(
        el("span", { class: "publish-status__mark", text: "✓", "aria-hidden": "true" }),
        el("span", { text: `Posted — your ${data.total_score}% is now on the PR.` })
      );
    } catch (e) {
      publishStatus.className = "publish-status publish-status--err";
      publishStatus.replaceChildren(
        el("span", { class: "publish-status__mark", text: "×", "aria-hidden": "true" }),
        el("span", { text: `Couldn't publish: ${e.message || e}` })
      );
      publishBtn.disabled = false;
    }
  });

  // Scroll to results, gently.
  requestAnimationFrame(() => {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

// ── submit ────────────────────────────────────────────────────────────────

submitBtn.addEventListener("click", async () => {
  submitBtn.disabled = true;
  if (submitLabel) submitLabel.textContent = "Grading…";

  const entries = quiz.questions.map((q) => {
    let value = "";
    if (q.type === "open") {
      const ta = document.querySelector(`textarea[name="${q.id}"]`);
      value = ta ? ta.value : "";
    } else {
      const checked = document.querySelector(`input[name="${q.id}"]:checked`);
      value = checked ? checked.value : "";
    }
    return { question_id: q.id, value };
  });
  try {
    const resp = await fetch("/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: "1", pr_number: quiz.pr_number, entries }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    lastResults = await resp.json();
    renderResults(lastResults);
    if (submitLabel) submitLabel.textContent = "Re-submit answers";
    submitBtn.disabled = false;
  } catch (e) {
    resultEl.innerHTML = "";
    resultEl.appendChild(
      el("div", {
        class: "submit-error",
        text: "Submission failed: " + (e.message || e),
      })
    );
    if (submitLabel) submitLabel.textContent = "Submit answers";
    submitBtn.disabled = false;
  }
});

render();
