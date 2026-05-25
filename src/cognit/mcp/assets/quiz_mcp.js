// cognit MCP browser: polls /state, renders the quiz once per change, posts answers.
// Re-renders only when the quiz's structure changes (so it survives replace_question)
// and never clobbers in-progress local input on a poll tick.
const root = document.getElementById("root");
let answers = {};
let sig = null;        // signature of the currently-rendered quiz
let resultsEl = null;

if (window.mermaid) window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

function quizSig(quiz) {
  return JSON.stringify(quiz.questions.map((q) => [
    q.id, q.type, q.prompt,
    q.type === "mcq" ? q.options : q.type === "mermaid" ? Object.keys(q.options) : null,
  ]));
}

async function postAnswer(qid, value) {
  answers[qid] = value;
  refreshSelections();
  try {
    const r = await fetch("/answer", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ question_id: qid, value }),
    });
    if (!r.ok) console.error("answer not saved:", r.status);
  } catch (e) {
    console.error("answer POST failed:", e);
  }
}

function optionEl(qid, key, label) {
  const el = document.createElement("label");
  el.className = "opt";
  el.dataset.qid = qid;
  el.dataset.val = key;
  el.textContent = label;
  el.onclick = () => postAnswer(qid, key);
  return el;
}

function renderQuestion(q, i) {
  const d = document.createElement("div");
  d.className = "q";
  const h = document.createElement("h3");
  h.textContent = `Question ${i + 1} · ${q.type}`;
  d.appendChild(h);
  const p = document.createElement("p");
  p.textContent = q.prompt;
  d.appendChild(p);
  if (q.type === "mcq") {
    q.options.forEach((o) => d.appendChild(optionEl(q.id, o, o)));
  } else if (q.type === "tf") {
    ["true", "false"].forEach((v) => d.appendChild(optionEl(q.id, v, v)));
  } else if (q.type === "mermaid") {
    Object.entries(q.options).forEach(([label, src]) => {
      const wrapper = document.createElement("div");
      wrapper.className = "opt";
      wrapper.dataset.qid = q.id;
      wrapper.dataset.val = label;
      wrapper.onclick = () => postAnswer(q.id, label);
      const caption = document.createElement("div");
      caption.textContent = `diagram ${label}`;
      wrapper.appendChild(caption);
      const merm = document.createElement("div");
      merm.className = "mermaid";
      merm.textContent = src;  // textContent only — never innerHTML (security)
      wrapper.appendChild(merm);
      d.appendChild(wrapper);
    });
  } else if (q.type === "open") {
    const ta = document.createElement("textarea");
    ta.dataset.qid = q.id;
    ta.value = answers[q.id] || "";
    ta.oninput = (e) => { answers[q.id] = e.target.value; };
    ta.onblur = () => postAnswer(q.id, ta.value);
    d.appendChild(ta);
  }
  return d;
}

function refreshSelections() {
  root.querySelectorAll(".opt").forEach((el) => {
    el.classList.toggle("sel", answers[el.dataset.qid] === el.dataset.val);
  });
}

async function renderQuiz(quiz) {
  root.innerHTML = "";
  resultsEl = null;
  quiz.questions.forEach((q, i) => root.appendChild(renderQuestion(q, i)));
  refreshSelections();
  if (window.mermaid) {
    await window.mermaid.run({ querySelector: "#root .mermaid" }).catch((e) => console.error("mermaid.run failed:", e));
  }
}

function showResults(results) {
  if (!resultsEl) {
    resultsEl = document.createElement("div");
    resultsEl.className = "result";
    root.appendChild(resultsEl);
  }
  resultsEl.textContent = `Score: ${results.total_score} / 100`;
}

async function tick() {
  let state;
  try {
    state = await (await fetch("/state")).json();
  } catch (e) {
    return;  // transient — retry next tick
  }
  if (!state.quiz) {
    root.textContent = "Waiting for the agent…";
    sig = null;
    return;
  }
  const newSig = quizSig(state.quiz);
  if (newSig !== sig) {
    sig = newSig;
    // Seed from server only for answers we don't already have locally (supports
    // resume without clobbering in-progress local selections).
    for (const [k, v] of Object.entries(state.answers || {})) {
      if (!(k in answers)) answers[k] = v;
    }
    renderQuiz(state.quiz);
  }
  if (state.results) showResults(state.results);
}

setInterval(tick, 1000);
tick();
