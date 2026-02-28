import { api, formatTime, getToken, wsUrl } from "../api.js";

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function addMessage(listEl, role, content, timestamp) {
  const cls = role === "user" ? "user" : "assistant";
  const safe = escapeHtml(content);
  const time = formatTime(timestamp);
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = `<div class="meta">${escapeHtml(role)} • ${escapeHtml(time)}</div><div>${safe}</div>`;
  listEl.appendChild(div);
  listEl.scrollTop = listEl.scrollHeight;
}

export async function renderChat(container) {
  container.innerHTML = `
    <div class="panel">
      <div class="row" style="justify-content: space-between">
        <div>
          <h2 style="margin: 0">Chat</h2>
          <div class="muted">Session: <span id="sessionId">connecting...</span></div>
        </div>
        <div class="muted" id="wsStatus">disconnected</div>
      </div>
      <div id="chatNotice" class="notice hidden" style="margin-top: 12px"></div>
      <div id="messages" style="height: 55vh; overflow: auto; margin-top: 12px;"></div>
      <div class="row" style="margin-top: 12px; align-items: flex-end">
        <div class="col">
          <label>Message</label>
          <textarea id="chatInput" placeholder="Type a message..."></textarea>
        </div>
        <div>
          <button id="sendBtn" class="btn" type="button">Send</button>
        </div>
      </div>
      <div id="chatError" class="error" style="margin-top: 10px"></div>
    </div>
  `;

  const token = getToken();
  const listEl = container.querySelector("#messages");
  const inputEl = container.querySelector("#chatInput");
  const sendBtn = container.querySelector("#sendBtn");
  const errEl = container.querySelector("#chatError");
  const sessionEl = container.querySelector("#sessionId");
  const statusEl = container.querySelector("#wsStatus");
  const noticeEl = container.querySelector("#chatNotice");

  let ws = null;
  let sessionId = "";

  function setStatus(s) {
    statusEl.textContent = s;
  }

  async function loadHistory() {
    if (!sessionId) return;
    const history = await api.get(`/api/chat/history?session_id=${encodeURIComponent(sessionId)}`);
    listEl.innerHTML = "";
    for (const m of history || []) {
      addMessage(listEl, m.role, m.content, m.timestamp);
    }
  }

  function connect() {
    errEl.textContent = "";
    ws = new WebSocket(wsUrl("/ws/chat", { token }));
    setStatus("connecting");

    ws.onopen = () => {
      setStatus("connected");
    };

    ws.onclose = () => {
      setStatus("disconnected");
    };

    ws.onerror = () => {
      errEl.textContent = "WebSocket error";
    };

    ws.onmessage = async (ev) => {
      const text = ev.data;
      // First message can be JSON session info.
      try {
        const data = JSON.parse(text);
        if (data && data.type === "session" && data.session_id) {
          sessionId = data.session_id;
          sessionEl.textContent = sessionId;
          await loadHistory();
          return;
        }
        if (data && data.type === "error") {
          const msg = String(data.detail || data.error || "Chat unavailable");
          errEl.textContent = msg;
          addMessage(listEl, "assistant", msg, Date.now());
          return;
        }
      } catch {
        // ignore
      }

      addMessage(listEl, "assistant", text, Date.now());
    };
  }

  sendBtn.addEventListener("click", () => {
    const text = (inputEl.value || "").trim();
    if (!text) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      errEl.textContent = "Not connected";
      return;
    }
    ws.send(text);
    addMessage(listEl, "user", text, Date.now());
    inputEl.value = "";
    inputEl.focus();
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      sendBtn.click();
    }
  });

  async function checkChatStatus() {
    if (!noticeEl) return;
    try {
      const cfg = await api.get("/api/chat/status");
      const kind = String(cfg && cfg.provider_kind ? cfg.provider_kind : "");
      const hasAnyKey = Boolean(cfg && cfg.has_any_api_key);
      if (kind === "oauth") return;
      if (hasAnyKey) return;

      const model = String((cfg && cfg.model) || "");
      const modelHtml = model
        ? `<div class="muted" style="margin-top: 4px">Current model: ${escapeHtml(model)}</div>`
        : "";

      noticeEl.classList.remove("hidden");
      noticeEl.classList.add("warn");
      noticeEl.innerHTML = `
        <div class="row" style="justify-content: space-between; gap: 12px">
          <div>
            <div style="font-weight: 700">Provider not configured</div>
            <div class="muted">Add an API key in Settings → Providers to enable chat responses.</div>
            ${modelHtml}
          </div>
          <button id="chatGoSettingsBtn" class="btn secondary" type="button">Open Settings</button>
        </div>
      `;
      const btn = noticeEl.querySelector("#chatGoSettingsBtn");
      if (btn) {
        btn.addEventListener("click", () => {
          window.location.hash = "#/settings";
        });
      }
    } catch {
      // ignore best-effort checks
    }
  }

  checkChatStatus().catch(() => {});
  connect();
  inputEl.focus();

  return () => {
    try {
      ws && ws.close();
    } catch {
      // ignore
    }
  };
}

