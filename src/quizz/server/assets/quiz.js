// mermaid is loaded via UMD script tag in index.html and attached to window.mermaid.
window.mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

const quiz = window.QUIZ;
const root = document.getElementById("quiz");

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
  // Render all mermaid diagrams now that the DOM is populated.
  try {
    await window.mermaid.run({ querySelector: ".mermaid" });
  } catch (e) {
    console.error("mermaid.run() failed", e);
  }
}

async function pollResults() {
  for (let i = 0; i < 120; i++) {
    const r = await fetch("/results");
    const data = await r.json();
    if (data.ready) {
      document.getElementById("result").textContent =
        "FINAL RESULTS:\n" + JSON.stringify(data.results, null, 2);
      return;
    }
    await new Promise((r) => setTimeout(r, 2500));
  }
  document.getElementById("result").textContent =
    "Results not back after 5 minutes — run `quizz take --show-results` later.";
}

document.getElementById("submit").addEventListener("click", async () => {
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
  const resp = await fetch("/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version: "1", pr_number: quiz.pr_number, entries }),
  });
  const data = await resp.json();
  document.getElementById("result").textContent =
    "Deterministic score (awaiting CI for open Q): " +
    JSON.stringify(data, null, 2);
  setTimeout(pollResults, 1500);
});

render();
