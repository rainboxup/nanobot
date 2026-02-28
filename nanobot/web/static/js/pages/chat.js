import { api, formatTime, getToken, wsUrl } from "../api.js";

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function roleLabel(role) {
  const r = String(role || "").toLowerCase();
  if (r === "user") return "用户";
  if (r === "assistant") return "助手";
  if (r === "system") return "系统";
  return String(role || "");
}

function addMessage(listEl, role, content, timestamp) {
  const cls = role === "user" ? "user" : "assistant";
  const safe = escapeHtml(content);
  const time = formatTime(timestamp);
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = `<div class="meta">${escapeHtml(roleLabel(role))} • ${escapeHtml(time)}</div><div>${safe}</div>`;
  listEl.appendChild(div);
  listEl.scrollTop = listEl.scrollHeight;
}

export async function renderChat(container) {
  container.innerHTML = `
    <div class="panel">
      <div class="row" style="justify-content: space-between">
        <div>
          <h2 style="margin: 0">对话</h2>
          <div class="muted">会话：<span id="sessionId">连接中...</span></div>
        </div>
        <div class="muted" id="wsStatus">未连接</div>
      </div>
      <div id="chatNotice" class="notice hidden" style="margin-top: 12px"></div>
      <div id="messages" style="height: 55vh; overflow: auto; margin-top: 12px;"></div>
      <div class="row" style="margin-top: 12px; align-items: flex-end">
        <div class="col">
          <label>消息</label>
          <textarea id="chatInput" placeholder="输入消息..."></textarea>
        </div>
        <div>
          <button id="sendBtn" class="btn" type="button">发送</button>
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
    setStatus("连接中");

    ws.onopen = () => {
      setStatus("已连接");
    };

    ws.onclose = () => {
      setStatus("未连接");
    };

    ws.onerror = () => {
      errEl.textContent = "WebSocket 错误";
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
          const msg = String(data.detail || data.error || "对话不可用");
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
      errEl.textContent = "未连接";
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
        ? `<div class="muted" style="margin-top: 4px">当前模型：${escapeHtml(model)}</div>`
        : "";

      noticeEl.classList.remove("hidden");
      noticeEl.classList.add("warn");
      noticeEl.innerHTML = `
        <div class="row" style="justify-content: space-between; gap: 12px">
          <div>
            <div style="font-weight: 700">未配置模型服务</div>
            <div class="muted">请在 设置 → 模型服务 中填写 API Key 以启用回复。</div>
            ${modelHtml}
          </div>
          <button id="chatGoSettingsBtn" class="btn secondary" type="button">打开设置</button>
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

