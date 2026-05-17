import mermaid from "/static/mermaid.esm.min.js";
mermaid.initialize({ startOnLoad: false });

const quiz = window.QUIZ;
const root = document.getElementById("quiz");

function escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function render() {
  for (const q of quiz.questions) {
    const section = document.createElement("section");
    const prompt = `<h3>${escape(q.id)} — ${escape(q.type)}</h3><p>${escape(q.prompt)}</p>`;
    if (q.type === "mcq") {
      const opts = q.options.map(opt =>
        `<label><input type="radio" name="${escape(q.id)}" value="${escape(opt)}"> ${escape(opt)}</label>`
      ).join("<br>");
      section.innerHTML = prompt + opts;
    } else if (q.type === "mermaid") {
      const opts = Object.entries(q.options).map(([label, src]) => {
        const renderId = `${escape(q.id)}_${escape(label)}`;
        return `
          <label class="mermaid-option">
            <input type="radio" name="${escape(q.id)}" value="${escape(label)}">
            <span>Option ${escape(label)}</span>
            <div class="mermaid" id="${renderId}">${escape(src)}</div>
          </label>`;
      }).join("");
      section.innerHTML = prompt + opts;
    } else if (q.type === "open") {
      section.innerHTML = prompt +
        `<textarea name="${escape(q.id)}" rows="6" placeholder="Your answer..."></textarea>`;
    } else if (q.type === "tf") {
      section.innerHTML = prompt +
        `<label><input type="radio" name="${escape(q.id)}" value="true"> true</label>
         <label><input type="radio" name="${escape(q.id)}" value="false"> false</label>`;
    }
    root.appendChild(section);
  }
  mermaid.run();
}

document.getElementById("submit").addEventListener("click", async () => {
  const entries = quiz.questions.map(q => {
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
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ version: "1", pr_number: quiz.pr_number, entries }),
  });
  const data = await resp.json();
  document.getElementById("result").textContent = JSON.stringify(data, null, 2);
});

render();
