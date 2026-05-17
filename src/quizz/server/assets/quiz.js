// mermaid is loaded via UMD script tag in index.html and attached to window.mermaid.
window.mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

const quiz = window.QUIZ;
const root = document.getElementById("quiz");
const submitBtn = document.getElementById("submit");
const resultEl = document.getElementById("result");

// Cached so the Publish button can re-send without re-grading.
let lastResults = null;

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "for") node.htmlFor = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child) node.appendChild(child);
  }
  return node;
}

function radio(name, value) {
  const i = document.createElement("input");
  i.type = "radio";
  i.name = name;
  i.value = value;
  return i;
}

function renderMCQ(q) {
  const section = el("section", {}, [
    el("h3", { text: `${q.id} — ${q.type}` }),
    el("p", { text: q.prompt }),
  ]);
  for (const opt of q.options) {
    section.appendChild(
      el("label", {}, [radio(q.id, opt), document.createTextNode(" " + opt)])
    );
  }
  return section;
}

function renderMermaid(q) {
  const section = el("section", {}, [
    el("h3", { text: `${q.id} — ${q.type}` }),
    el("p", { text: q.prompt }),
  ]);
  for (const [label, src] of Object.entries(q.options)) {
    const diagram = el("div", { class: "mermaid", id: `${q.id}_${label}` });
    diagram.textContent = src; // raw mermaid source — no HTML escaping needed
    section.appendChild(
      el("label", { class: "mermaid-option" }, [
        radio(q.id, label),
        el("span", { text: ` Option ${label} ` }),
        diagram,
      ])
    );
  }
  return section;
}

function renderOpen(q) {
  const ta = document.createElement("textarea");
  ta.name = q.id;
  ta.rows = 6;
  ta.placeholder = "Your answer...";
  return el("section", {}, [
    el("h3", { text: `${q.id} — ${q.type}` }),
    el("p", { text: q.prompt }),
    ta,
  ]);
}

function renderTF(q) {
  return el("section", {}, [
    el("h3", { text: `${q.id} — ${q.type}` }),
    el("p", { text: q.prompt }),
    el("label", {}, [radio(q.id, "true"), document.createTextNode(" true")]),
    el("label", {}, [radio(q.id, "false"), document.createTextNode(" false")]),
  ]);
}

async function render() {
  for (const q of quiz.questions) {
    let section;
    if (q.type === "mcq") section = renderMCQ(q);
    else if (q.type === "mermaid") section = renderMermaid(q);
    else if (q.type === "open") section = renderOpen(q);
    else if (q.type === "tf") section = renderTF(q);
    if (section) root.appendChild(section);
  }
  try {
    await window.mermaid.run({ querySelector: ".mermaid" });
  } catch (e) {
    console.error("mermaid.run() failed", e);
  }
}

function renderResults(results) {
  // Build a friendly result panel: total score + per-question breakdown + Publish button.
  resultEl.innerHTML = "";
  const summary = el("div", { class: "result-summary" }, [
    el("h2", { text: `Total: ${results.total_score}%` }),
  ]);
  resultEl.appendChild(summary);

  const list = el("ul", { class: "result-list" });
  for (const r of results.per_question) {
    const icon = r.correct ? "✅" : "❌";
    const li = el("li", { class: r.correct ? "ok" : "bad" }, [
      el("strong", { text: `${icon} ${r.question_id} — ${r.score}%` }),
    ]);
    if (r.feedback) {
      li.appendChild(el("blockquote", { text: r.feedback }));
    }
    list.appendChild(li);
  }
  resultEl.appendChild(list);

  // Publish button — opt-in.
  const publishBtn = el("button", {
    id: "publish",
    class: "publish",
    text: "Publish results to PR",
  });
  const publishStatus = el("span", { id: "publish-status", class: "publish-status" });
  resultEl.appendChild(publishBtn);
  resultEl.appendChild(publishStatus);

  publishBtn.addEventListener("click", async () => {
    publishBtn.disabled = true;
    publishStatus.textContent = " Posting…";
    try {
      const r = await fetch("/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastResults),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      publishStatus.textContent = ` ✓ Posted (total ${data.total_score}%)`;
    } catch (e) {
      publishStatus.textContent = ` ✗ Failed: ${e}`;
      publishBtn.disabled = false;
    }
  });
}

submitBtn.addEventListener("click", async () => {
  submitBtn.disabled = true;
  submitBtn.textContent = "Grading…";
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
    submitBtn.textContent = "Re-submit";
    submitBtn.disabled = false;
  } catch (e) {
    resultEl.textContent = "Submission failed: " + e;
    submitBtn.disabled = false;
  }
});

render();
