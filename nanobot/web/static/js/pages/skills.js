import { api } from "../api.js";

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export async function renderSkills(container) {
  container.innerHTML = `<div class="panel"><h2 style="margin-top:0">Skills</h2><div class="muted">Loading...</div></div>`;

  const skills = await api.get("/api/skills");
  const list = el(`<div class="panel">
      <div class="row" style="justify-content: space-between">
        <h2 style="margin:0">Skills</h2>
        <div class="muted">${(skills || []).length} total</div>
      </div>
      <div class="grid" style="margin-top: 12px" id="grid"></div>
      <div id="detail" style="margin-top: 12px"></div>
    </div>`);

  const grid = list.querySelector("#grid");
  const detail = list.querySelector("#detail");

  async function showDetail(name) {
    detail.innerHTML = `<div class="muted">Loading ${escapeHtml(name)}...</div>`;
    const s = await api.get(`/api/skills/${encodeURIComponent(name)}`);
    detail.innerHTML = `
      <div class="panel" style="margin-top: 12px">
        <div class="row" style="justify-content: space-between">
          <div>
            <div><span class="badge">${escapeHtml(s.name)}</span> <span class="muted">${escapeHtml(s.source || "")}</span></div>
            <div class="muted" style="margin-top: 4px">${escapeHtml(s.description || "")}</div>
          </div>
          <button class="btn secondary" type="button" id="closeSkill">Close</button>
        </div>
        <pre class="code" style="margin-top: 12px; white-space: pre-wrap">${escapeHtml(s.content || "")}</pre>
      </div>`;

    detail.querySelector("#closeSkill").addEventListener("click", () => {
      detail.innerHTML = "";
    });
  }

  for (const s of skills || []) {
    const card = el(`<div class="card">
        <div class="row" style="justify-content: space-between">
          <div><span class="badge">${escapeHtml(s.name)}</span></div>
          <div class="muted">${escapeHtml(s.source || "")}</div>
        </div>
        <div class="muted" style="margin-top: 8px">${escapeHtml(s.description || "")}</div>
      </div>`);

    card.addEventListener("click", () => {
      showDetail(s.name).catch(() => {});
    });
    grid.appendChild(card);
  }

  container.innerHTML = "";
  container.appendChild(list);

  return () => {};
}

