import { api, getToken } from "../api.js";

const SENSITIVE_KEYS = new Set([
  "token",
  "secret",
  "app_secret",
  "client_secret",
  "encrypt_key",
  "verification_token",
  "imap_password",
  "smtp_password",
  "bot_token",
  "app_token",
]);

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function roleLabel(role) {
  const r = String(role || "").toLowerCase();
  if (r === "member") return "成员";
  if (r === "admin") return "管理员";
  if (r === "owner") return "拥有者";
  return String(role || "");
}

function formatDateTime(value) {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return "";
  }
}

function setByPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i];
    if (!cur[k] || typeof cur[k] !== "object") cur[k] = {};
    cur = cur[k];
  }
  cur[parts[parts.length - 1]] = value;
}

function renderConfigForm(name, config, onSave) {
  const form = el(`<form class="panel" style="margin-top: 12px">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">编辑：${escapeHtml(name)}</h3>
        <div>
          <button class="btn secondary" type="button" data-action="cancel">关闭</button>
          <button class="btn" type="submit">保存</button>
        </div>
      </div>
      <div class="muted" style="margin-top: 6px">
        敏感字段不会自动填充；留空将保持不变。
      </div>
      <div data-fields style="margin-top: 12px"></div>
      <div class="error" data-error style="margin-top: 10px"></div>
    </form>`);

  const fields = form.querySelector("[data-fields]");
  const errEl = form.querySelector("[data-error]");

  function renderFields(obj, prefix, target) {
    for (const [k, v] of Object.entries(obj || {})) {
      const path = prefix ? `${prefix}.${k}` : k;

      if (v && typeof v === "object" && !Array.isArray(v)) {
        const box = el(
          `<div style="margin-top: 10px; padding: 10px; border: 1px solid var(--border); border-radius: 10px;">
            <div class="muted" style="margin-bottom: 8px">${escapeHtml(path)}</div>
            <div data-box-fields></div>
          </div>`
        );
        target.appendChild(box);
        renderFields(v, path, box.querySelector("[data-box-fields]"));
        continue;
      }

      let input = null;
      const label = el(`<label style="display:block; margin-top: 10px">${escapeHtml(path)}</label>`);

      if (typeof v === "boolean") {
        input = el(`<input type="checkbox" data-path="${escapeHtml(path)}" />`);
        input.dataset.kind = "bool";
        input.checked = v;
      } else if (typeof v === "number") {
        input = el(`<input type="number" data-path="${escapeHtml(path)}" />`);
        input.dataset.kind = "number";
        input.value = String(v);
      } else if (Array.isArray(v)) {
        input = el(`<input type="text" data-path="${escapeHtml(path)}" />`);
        input.dataset.kind = "array";
        input.value = v.join(", ");
      } else {
        const isSensitive = SENSITIVE_KEYS.has(k);
        input = el(
          `<input type="${isSensitive ? "password" : "text"}" data-path="${escapeHtml(path)}" />`
        );
        input.dataset.kind = isSensitive ? "sensitive" : "string";
        if (!isSensitive) {
          input.value = String(v ?? "");
        } else {
          input.placeholder = "保持不变";
        }
      }

      target.appendChild(label);
      target.appendChild(input);
    }
  }

  renderFields(config, "", fields);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.textContent = "";

    const update = {};
    for (const input of form.querySelectorAll("[data-path]")) {
      const path = input.getAttribute("data-path");
      const kind = input.dataset.kind || "string";

      if (kind === "bool") {
        setByPath(update, path, Boolean(input.checked));
        continue;
      }

      if (kind === "number") {
        if (input.value === "") continue;
        setByPath(update, path, Number(input.value));
        continue;
      }

      if (kind === "array") {
        const raw = String(input.value || "");
        const items = raw
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean);
        setByPath(update, path, items);
        continue;
      }

      if (kind === "sensitive") {
        const raw = String(input.value || "");
        if (!raw) continue;
        setByPath(update, path, raw);
        continue;
      }

      // string
      setByPath(update, path, String(input.value || ""));
    }

    try {
      await onSave(update);
    } catch (err) {
      errEl.textContent = err.message || "保存失败";
    }
  });

  form.querySelector('[data-action="cancel"]').addEventListener("click", () => {
    form.remove();
  });

  return form;
}

async function renderProviders(container) {
  const panel = container.querySelector("#settingsPanel");
  panel.innerHTML = `<div class="panel"><h3 style="margin-top:0">模型服务</h3><div class="muted">加载中...</div></div>`;

  const providers = await api.get("/api/providers");

  const table = el(`<div class="panel">
      <h3 style="margin-top:0">模型服务</h3>
      <table class="table">
        <thead>
          <tr><th>名称</th><th>API 地址</th><th>密钥</th><th></th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <div class="muted" style="margin-top:10px">返回结果中的密钥始终会被打码。</div>
    </div>`);

  const tbody = table.querySelector("tbody");

  for (const p of providers || []) {
    const tr = el(`<tr>
        <td><span class="badge">${escapeHtml(p.name)}</span></td>
        <td class="code">${escapeHtml(p.api_base || "")}</td>
        <td class="code">${escapeHtml(p.masked_key || "")}</td>
        <td><button class="btn secondary" type="button">编辑</button></td>
      </tr>`);

    tr.querySelector("button").addEventListener("click", async () => {
      // Inline edit row (no secret prefill).
      const formRow = el(`<tr>
          <td colspan="4">
            <div class="row" style="align-items: flex-end">
              <div class="col">
                <label>api_base</label>
                <input type="text" value="${escapeHtml(p.api_base || "")}" data-field="api_base" />
              </div>
              <div class="col">
                <label>api_key</label>
                <input type="password" placeholder="（留空保持不变）" data-field="api_key" />
                <label class="muted" style="display:flex; align-items:center; gap:8px; margin-top:8px">
                  <input type="checkbox" data-field="clear_key" />
                  清空密钥
                </label>
              </div>
              <div class="row" style="justify-content: flex-end">
                <button class="btn secondary" type="button" data-action="cancel">取消</button>
                <button class="btn" type="button" data-action="save">保存</button>
              </div>
            </div>
            <div class="error" data-error style="margin-top: 8px"></div>
          </td>
        </tr>`);

      // Remove any existing inline forms.
      for (const old of tbody.querySelectorAll("tr[data-inline='1']")) old.remove();
      formRow.dataset.inline = "1";
      tr.insertAdjacentElement("afterend", formRow);

      const errEl = formRow.querySelector("[data-error]");
      formRow.querySelector('[data-action="cancel"]').addEventListener("click", () => {
        formRow.remove();
      });

      formRow.querySelector('[data-action="save"]').addEventListener("click", async () => {
        errEl.textContent = "";
        const apiBase = formRow.querySelector('[data-field="api_base"]').value;
        const apiKey = formRow.querySelector('[data-field="api_key"]').value;
        const clearKey = Boolean(formRow.querySelector('[data-field="clear_key"]').checked);
        try {
          const payload = { api_base: apiBase };
          // Only send api_key when user typed something. This avoids overwriting with empty.
          if (apiKey !== "") payload.api_key = apiKey;
          else if (clearKey) payload.api_key = "";
          await api.put(`/api/providers/${encodeURIComponent(p.name)}`, payload);
          await renderProviders(container);
        } catch (err) {
          errEl.textContent = err.message || "保存失败";
        }
      });
    });

    tbody.appendChild(tr);
  }

  panel.innerHTML = "";
  panel.appendChild(table);
}

async function renderChannels(container) {
  const panel = container.querySelector("#settingsPanel");
  panel.innerHTML = `<div class="panel"><h3 style="margin-top:0">渠道</h3><div class="muted">加载中...</div></div>`;

  const channels = await api.get("/api/channels");
  const wrap = el(`<div class="panel"><h3 style="margin-top:0">渠道</h3><div id="list"></div></div>`);
  const list = wrap.querySelector("#list");

  for (const ch of channels || []) {
    const item = el(`<div style="padding:10px 0; border-bottom: 1px solid var(--border);">
        <div class="row" style="justify-content: space-between">
          <div>
            <span class="badge">${escapeHtml(ch.name)}</span>
            <span class="muted" style="margin-left: 8px">${escapeHtml(JSON.stringify(ch.config_summary || {}))}</span>
          </div>
          <div class="row">
            <label class="muted">启用</label>
            <input type="checkbox" ${ch.enabled ? "checked" : ""} />
            <button class="btn secondary" type="button">编辑</button>
          </div>
        </div>
      </div>`);

    const toggle = item.querySelector('input[type="checkbox"]');
    toggle.addEventListener("change", async () => {
      await api.post(`/api/channels/${encodeURIComponent(ch.name)}/toggle`, {});
      await renderChannels(container);
    });

    item.querySelector("button").addEventListener("click", async () => {
      const detail = await api.get(`/api/channels/${encodeURIComponent(ch.name)}`);
      const form = renderConfigForm(ch.name, detail.config || {}, async (update) => {
        await api.put(`/api/channels/${encodeURIComponent(ch.name)}`, update);
        form.remove();
        await renderChannels(container);
      });
      wrap.appendChild(form);
      form.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    list.appendChild(item);
  }

  panel.innerHTML = "";
  panel.appendChild(wrap);
}

async function renderBetaAccess(container) {
  const panel = container.querySelector("#settingsPanel");
  panel.innerHTML = `<div class="panel"><h3 style="margin-top:0">封闭 Beta</h3><div class="muted">加载中...</div></div>`;

  const allowlist = await api.get("/api/beta/allowlist");
  const invites = await api.get("/api/beta/invites");

  const wrap = el(`<div class="panel">
      <h3 style="margin-top:0">封闭 Beta 访问</h3>
      <div class="muted" style="margin-top:6px">
        封闭 Beta 模式：<span class="badge">${allowlist.closed_beta ? "已启用" : "未启用"}</span>
      </div>

      <div style="margin-top:14px">
        <div class="row" style="justify-content: space-between">
          <h4 style="margin:0">白名单（${allowlist.count || 0}）</h4>
          <div class="row">
            <input id="betaUserInput" type="text" placeholder="用户名" style="min-width: 220px" />
            <button class="btn" type="button" id="betaAddUserBtn">添加</button>
          </div>
        </div>
        <div id="betaUsers" style="margin-top:10px"></div>
      </div>

      <div style="margin-top:20px">
        <div class="row" style="justify-content: space-between">
          <h4 style="margin:0">邀请码</h4>
          <div class="row">
            <input id="inviteForUser" type="text" placeholder="指定用户名（可选）" style="min-width: 180px" />
            <input id="inviteTtlHours" type="number" min="1" value="72" style="width: 110px" />
            <input id="inviteMaxUses" type="number" min="1" value="1" style="width: 90px" />
            <button class="btn" type="button" id="createInviteBtn">创建邀请码</button>
          </div>
        </div>
        <div class="muted" style="margin-top:6px">将邀请码发送给用户；用户可使用 invite_code 登录一次以加入白名单。</div>
        <table class="table" style="margin-top:10px">
          <thead>
            <tr><th>邀请码</th><th>目标用户</th><th>使用次数</th><th>过期时间</th><th>状态</th><th></th></tr>
          </thead>
          <tbody id="inviteRows"></tbody>
        </table>
      </div>
    </div>`);

  const usersEl = wrap.querySelector("#betaUsers");
  for (const user of allowlist.users || []) {
    const row = el(`<div class="row" style="justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border);">
        <div><span class="badge">${escapeHtml(user)}</span></div>
        <button class="btn secondary" type="button">移除</button>
      </div>`);
    row.querySelector("button").addEventListener("click", async () => {
      await api.delete(`/api/beta/allowlist/${encodeURIComponent(user)}`);
      await renderBetaAccess(container);
    });
    usersEl.appendChild(row);
  }

  const userInput = wrap.querySelector("#betaUserInput");
  wrap.querySelector("#betaAddUserBtn").addEventListener("click", async () => {
    const username = String(userInput.value || "").trim();
    if (!username) return;
    await api.post("/api/beta/allowlist", { username });
    await renderBetaAccess(container);
  });

  const inviteRows = wrap.querySelector("#inviteRows");
  for (const inv of invites || []) {
    const row = el(`<tr>
        <td class="code">${escapeHtml(inv.code || "")}</td>
        <td>${escapeHtml(inv.for_username || "-")}</td>
        <td>${escapeHtml(String(inv.used_count || 0))} / ${escapeHtml(String(inv.max_uses || 1))}</td>
        <td>${escapeHtml(formatDateTime(inv.expires_at || ""))}</td>
        <td>${inv.active ? '<span class="badge">有效</span>' : '<span class="badge">已失效</span>'}</td>
        <td><button class="btn secondary" type="button">撤销</button></td>
      </tr>`);
    row.querySelector("button").addEventListener("click", async () => {
      await api.delete(`/api/beta/invites/${encodeURIComponent(inv.code || "")}`);
      await renderBetaAccess(container);
    });
    inviteRows.appendChild(row);
  }

  wrap.querySelector("#createInviteBtn").addEventListener("click", async () => {
    const forUsername = String(wrap.querySelector("#inviteForUser").value || "").trim();
    const ttlHours = Number(wrap.querySelector("#inviteTtlHours").value || 72);
    const maxUses = Number(wrap.querySelector("#inviteMaxUses").value || 1);
    const payload = { ttl_hours: ttlHours, max_uses: maxUses };
    if (forUsername) payload.for_username = forUsername;
    await api.post("/api/beta/invites", payload);
    await renderBetaAccess(container);
  });

  panel.innerHTML = "";
  panel.appendChild(wrap);
}

async function renderUsers(container) {
  const panel = container.querySelector("#settingsPanel");
  panel.innerHTML = `<div class="panel"><h3 style="margin-top:0">用户</h3><div class="muted">加载中...</div></div>`;

  let me = null;
  let users = [];
  try {
    [me, users] = await Promise.all([api.get("/api/auth/me"), api.get("/api/auth/users")]);
  } catch (err) {
    const msg = String((err && err.message) || "加载失败");
    panel.innerHTML = `<div class="panel"><h3 style="margin-top:0">用户</h3><div class="error">${escapeHtml(msg)}</div></div>`;
    return;
  }

  const isOwner = String((me && me.role) || "").toLowerCase() === "owner";
  const meUsername = String((me && me.username) || "").toLowerCase();
  const meTenant = String((me && me.tenant_id) || "").toLowerCase();
  const roleOptions = isOwner ? ["member", "admin", "owner"] : ["member", "admin"];
  const wrap = el(`<div class="panel">
      <h3 style="margin-top:0">用户与权限</h3>
      <div class="muted" style="margin-top:6px">当前角色：<span class="badge">${escapeHtml(roleLabel(me.role || "-"))}</span></div>

      <div style="margin-top: 14px">
        <div class="row" style="justify-content: space-between; align-items: center;">
          <h4 style="margin: 0">创建用户</h4>
          <button class="btn secondary" type="button" id="usersReloadBtn">刷新</button>
        </div>
        <div class="row" style="margin-top: 10px; gap: 8px; flex-wrap: wrap;">
          <input id="usersCreateName" type="text" placeholder="用户名" style="min-width: 160px" />
          <input id="usersCreatePassword" type="password" placeholder="密码（>=6）" style="min-width: 180px" />
          <select id="usersCreateRole">
            ${roleOptions
              .map(
                (role) =>
                  `<option value="${escapeHtml(role)}">${escapeHtml(roleLabel(role))}</option>`
              )
              .join("")}
          </select>
          <input id="usersCreateTenant" type="text" placeholder="租户ID（可选）" style="min-width: 160px" />
          <button class="btn" type="button" id="usersCreateBtn">创建</button>
        </div>
        <div class="muted" style="margin-top: 6px">Admin 只能在自己的租户内创建用户。</div>
      </div>

      <div style="margin-top: 18px">
        <h4 style="margin: 0">用户列表</h4>
        <div class="error" id="usersError" style="margin-top: 8px"></div>
        <table class="table" style="margin-top:10px">
          <thead>
            <tr><th>用户名</th><th>租户</th><th>角色</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody id="usersRows"></tbody>
        </table>
      </div>

      <div style="margin-top: 20px">
        <h4 style="margin: 0">修改我的密码</h4>
        <div class="row" style="margin-top: 10px; gap: 8px; flex-wrap: wrap;">
          <input id="usersOldPassword" type="password" placeholder="旧密码" style="min-width: 180px" />
          <input id="usersNewPassword" type="password" placeholder="新密码（>=6）" style="min-width: 180px" />
          <input id="usersNewPasswordConfirm" type="password" placeholder="确认新密码" style="min-width: 180px" />
          <button class="btn secondary" type="button" id="usersChangePasswordBtn">更新密码</button>
        </div>
      </div>
    </div>`);

  const rowsEl = wrap.querySelector("#usersRows");
  const errEl = wrap.querySelector("#usersError");
  const createName = wrap.querySelector("#usersCreateName");
  const createPassword = wrap.querySelector("#usersCreatePassword");
  const createRole = wrap.querySelector("#usersCreateRole");
  const createTenant = wrap.querySelector("#usersCreateTenant");
  const createBtn = wrap.querySelector("#usersCreateBtn");
  const reloadBtn = wrap.querySelector("#usersReloadBtn");
  const oldPwd = wrap.querySelector("#usersOldPassword");
  const newPwd = wrap.querySelector("#usersNewPassword");
  const newPwdConfirm = wrap.querySelector("#usersNewPasswordConfirm");
  const changePwdBtn = wrap.querySelector("#usersChangePasswordBtn");

  async function loadUsers() {
    users = await api.get("/api/auth/users");
  }

  async function promptResetPassword(username) {
    return new Promise((resolve) => {
      const overlay = el(`<div style="
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.45);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 16px;
          z-index: 2000;
        ">
          <div class="panel" style="width: 100%; max-width: 460px;">
            <h4 style="margin-top:0">重置密码</h4>
            <div class="muted">用户：<span class="badge">${escapeHtml(username)}</span></div>
            <div class="row" style="margin-top: 12px; gap: 8px; flex-wrap: wrap;">
              <input id="resetPwdValue" type="password" placeholder="新密码（>=6）" style="flex: 1; min-width: 200px" />
            </div>
            <div class="row" style="margin-top: 8px; gap: 8px; flex-wrap: wrap;">
              <input id="resetPwdConfirm" type="password" placeholder="确认新密码" style="flex: 1; min-width: 200px" />
            </div>
            <div class="error" id="resetPwdError" style="margin-top: 8px"></div>
            <div class="row" style="justify-content: flex-end; margin-top: 12px; gap: 8px;">
              <button class="btn secondary" type="button" data-action="cancel">取消</button>
              <button class="btn" type="button" data-action="submit">确认</button>
            </div>
          </div>
        </div>`);

      function close(value) {
        overlay.remove();
        resolve(value);
      }

      const pwdEl = overlay.querySelector("#resetPwdValue");
      const confirmEl = overlay.querySelector("#resetPwdConfirm");
      const errorEl = overlay.querySelector("#resetPwdError");
      overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => close(null));
      overlay.querySelector('[data-action="submit"]').addEventListener("click", () => {
        const pwd = String(pwdEl.value || "");
        const confirm = String(confirmEl.value || "");
        if (pwd.length < 6) {
          errorEl.textContent = "密码至少 6 位。";
          return;
        }
        if (pwd !== confirm) {
          errorEl.textContent = "两次输入的密码不一致。";
          return;
        }
        close(pwd);
      });
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close(null);
      });
      document.body.appendChild(overlay);
      pwdEl.focus();
    });
  }

  async function openSessionManager(username) {
    const overlay = el(`<div style="
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.45);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
        z-index: 2000;
      ">
        <div class="panel" style="width: 100%; max-width: 860px; max-height: 82vh; overflow: auto;">
          <div class="row" style="justify-content: space-between; align-items: center;">
            <h4 style="margin: 0">会话</h4>
            <div class="row" style="gap: 8px; flex-wrap: wrap;">
              <button class="btn secondary" type="button" data-action="refresh">刷新</button>
              <button class="btn secondary" type="button" data-action="revoke-all">强制全部退出</button>
              <button class="btn secondary" type="button" data-action="close">关闭</button>
            </div>
          </div>
          <div class="muted" style="margin-top: 8px">
            用户：<span class="badge">${escapeHtml(username)}</span>
          </div>
          <div class="row" style="margin-top: 8px; gap: 8px; flex-wrap: wrap;">
            <input id="sessionReasonInput" type="text" placeholder="撤销原因（必填）" style="min-width: 260px; flex: 1" />
          </div>
          <div class="muted" id="sessionSummary" style="margin-top: 8px"></div>
          <div class="error" id="sessionError" style="margin-top: 8px"></div>
          <table class="table" style="margin-top:10px">
            <thead>
              <tr><th>Token ID</th><th>状态</th><th>签发时间</th><th>过期时间</th><th>撤销时间</th><th></th></tr>
            </thead>
            <tbody id="sessionRows"></tbody>
          </table>
          <div class="muted" id="sessionEmpty" style="margin-top: 8px"></div>
        </div>
      </div>`);

    const rowsEl = overlay.querySelector("#sessionRows");
    const summaryEl = overlay.querySelector("#sessionSummary");
    const errEl2 = overlay.querySelector("#sessionError");
    const emptyEl2 = overlay.querySelector("#sessionEmpty");
    const reasonInput = overlay.querySelector("#sessionReasonInput");
    const refreshBtn = overlay.querySelector('[data-action="refresh"]');
    const revokeAllBtn = overlay.querySelector('[data-action="revoke-all"]');
    const closeBtn = overlay.querySelector('[data-action="close"]');

    let loading = false;
    let rows = [];

    function setBusy(disabled) {
      refreshBtn.disabled = disabled;
      revokeAllBtn.disabled = disabled;
      reasonInput.disabled = disabled;
    }

    function requireSessionReason() {
      const reason = String(reasonInput.value || "").trim();
      if (!reason) {
        errEl2.textContent = "请填写原因。";
        reasonInput.focus();
        return null;
      }
      return reason;
    }

    function renderRows() {
      rowsEl.innerHTML = "";
      for (const item of rows) {
        const tokenId = String((item && item.token_id) || "");
        const active = Boolean(item && item.active);
        const revokedAt = String((item && item.revoked_at) || "");
        const status = revokedAt ? "revoked" : active ? "active" : "expired";
        const statusLabel =
          status === "active" ? "有效" : status === "revoked" ? "已撤销" : status === "expired" ? "已过期" : status;
        const statusBadge =
          status === "active" ? `<span class="badge">${escapeHtml(statusLabel)}</span>` : escapeHtml(statusLabel);
        const row = el(`<tr>
            <td class="code">${escapeHtml(tokenId || "-")}</td>
            <td>${statusBadge}</td>
            <td>${escapeHtml(formatDateTime((item && item.issued_at) || ""))}</td>
            <td>${escapeHtml(formatDateTime((item && item.expires_at) || ""))}</td>
            <td>${escapeHtml(formatDateTime(revokedAt || ""))}</td>
            <td>${active ? '<button class="btn secondary" type="button" data-action="revoke-one">撤销</button>' : ""}</td>
          </tr>`);
        const revokeOneBtn = row.querySelector('[data-action="revoke-one"]');
        if (revokeOneBtn) {
          revokeOneBtn.addEventListener("click", async () => {
            if (loading) return;
            const reason = requireSessionReason();
            if (!reason) return;
            if (!window.confirm(`确定撤销用户 "${username}" 的该会话吗？`)) return;
            errEl2.textContent = "";
            try {
              loading = true;
              setBusy(true);
              const reasonQuery = encodeURIComponent(reason);
              await api.delete(
                `/api/auth/users/${encodeURIComponent(username)}/sessions/${encodeURIComponent(tokenId)}?reason=${reasonQuery}`
              );
              await fetchRows();
            } catch (err) {
              errEl2.textContent = String((err && err.message) || "撤销会话失败");
            } finally {
              loading = false;
              setBusy(false);
            }
          });
        }
        rowsEl.appendChild(row);
      }
      emptyEl2.textContent = rows.length ? "" : "未找到会话。";
    }

    async function fetchRows() {
      const data = await api.get(
        `/api/auth/users/${encodeURIComponent(username)}/sessions?include_revoked=true&limit=200`
      );
      rows = Array.isArray(data && data.sessions) ? data.sessions : [];
      const activeCount = Number((data && data.active_session_count) || 0);
      const totalCount = Number((data && data.session_count) || rows.length);
      summaryEl.textContent = `会话总数：${totalCount}；活跃会话：${activeCount}`;
      renderRows();
    }

    async function loadRows() {
      if (loading) return;
      loading = true;
      setBusy(true);
      errEl2.textContent = "";
      try {
        await fetchRows();
      } catch (err) {
        errEl2.textContent = String((err && err.message) || "加载会话失败");
      } finally {
        loading = false;
        setBusy(false);
      }
    }

    refreshBtn.addEventListener("click", () => {
      loadRows().catch(() => {});
    });
    revokeAllBtn.addEventListener("click", async () => {
      if (loading) return;
      const reason = requireSessionReason();
      if (!reason) return;
      if (!window.confirm(`确定强制注销用户 "${username}" 的所有活跃会话吗？`)) return;
      errEl2.textContent = "";
      try {
        loading = true;
        setBusy(true);
        await api.post(`/api/auth/users/${encodeURIComponent(username)}/sessions/revoke-all`, { reason });
        await fetchRows();
      } catch (err) {
        errEl2.textContent = String((err && err.message) || "强制全部退出失败");
      } finally {
        loading = false;
        setBusy(false);
      }
    });

    function close() {
      overlay.remove();
    }
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });

    document.body.appendChild(overlay);
    reasonInput.focus();
    await loadRows();
  }

  async function renderUserRows() {
    rowsEl.innerHTML = "";
    const list = Array.isArray(users) ? users : [];
    const canManageLifecycle = (item) => {
      const username = String((item && item.username) || "").toLowerCase();
      if (!username || username === meUsername) return false;
      if (isOwner) return true;
      const tenant = String((item && item.tenant_id) || "").toLowerCase();
      const role = String((item && item.role) || "").toLowerCase();
      return tenant === meTenant && role === "member";
    };
    for (const item of list) {
      const username = String(item.username || "");
      const canEditRole = isOwner && username && username !== String(me.username || "");
      const canManage = canManageLifecycle(item);
      const isActive = Boolean(item.active);
      const row = el(`<tr>
          <td><span class="badge">${escapeHtml(username || "-")}</span></td>
          <td>${escapeHtml(item.tenant_id || "-")}</td>
          <td>${canEditRole ? `<select data-action="role-select">
              <option value="member"${String(item.role) === "member" ? " selected" : ""}>${escapeHtml(
                roleLabel("member")
              )}</option>
              <option value="admin"${String(item.role) === "admin" ? " selected" : ""}>${escapeHtml(
                roleLabel("admin")
              )}</option>
              <option value="owner"${String(item.role) === "owner" ? " selected" : ""}>${escapeHtml(
                roleLabel("owner")
              )}</option>
            </select>` : escapeHtml(roleLabel(item.role || "-"))}</td>
          <td>${isActive ? '<span class="badge">启用</span>' : '<span class="badge">禁用</span>'}</td>
          <td>
            <div class="row" style="gap: 8px; flex-wrap: wrap;">
              ${canEditRole ? '<button class="btn secondary" type="button" data-action="update-role">更新角色</button>' : ""}
              ${canManage ? `<button class="btn secondary" type="button" data-action="toggle-status">${isActive ? "禁用" : "启用"}</button>` : ""}
              ${canManage ? '<button class="btn secondary" type="button" data-action="manage-sessions">会话</button>' : ""}
              ${canManage ? '<button class="btn secondary" type="button" data-action="delete-user">删除</button>' : ""}
              ${canManage ? '<button class="btn secondary" type="button" data-action="reset-password">重置密码</button>' : ""}
            </div>
          </td>
        </tr>`);

      const roleBtn = row.querySelector('[data-action="update-role"]');
      if (roleBtn) {
        roleBtn.addEventListener("click", async () => {
          errEl.textContent = "";
          try {
            const nextRole = String(row.querySelector('[data-action="role-select"]').value || "").trim();
            await api.put(`/api/auth/users/${encodeURIComponent(username)}/role`, { role: nextRole });
            await loadUsers();
            await renderUserRows();
          } catch (err) {
            errEl.textContent = String((err && err.message) || "更新角色失败");
          }
        });
      }

      const toggleBtn = row.querySelector('[data-action="toggle-status"]');
      if (toggleBtn) {
        toggleBtn.addEventListener("click", async () => {
          errEl.textContent = "";
          const nextActive = !isActive;
          const actionText = nextActive ? "启用" : "禁用";
          if (!window.confirm(`确定要${actionText}用户 "${username}" 吗？`)) return;
          try {
            await api.put(`/api/auth/users/${encodeURIComponent(username)}/status`, { active: nextActive });
            await loadUsers();
            await renderUserRows();
          } catch (err) {
            errEl.textContent = String((err && err.message) || "更新用户状态失败");
          }
        });
      }

      const deleteBtn = row.querySelector('[data-action="delete-user"]');
      if (deleteBtn) {
        deleteBtn.addEventListener("click", async () => {
          errEl.textContent = "";
          if (!window.confirm(`确定永久删除用户 "${username}" 吗？此操作不可撤销。`)) return;
          try {
            await api.delete(`/api/auth/users/${encodeURIComponent(username)}`);
            await loadUsers();
            await renderUserRows();
          } catch (err) {
            errEl.textContent = String((err && err.message) || "删除用户失败");
          }
        });
      }

      const manageSessionsBtn = row.querySelector('[data-action="manage-sessions"]');
      if (manageSessionsBtn) {
        manageSessionsBtn.addEventListener("click", async () => {
          errEl.textContent = "";
          try {
            await openSessionManager(username);
          } catch (err) {
            errEl.textContent = String((err && err.message) || "打开会话失败");
          }
        });
      }

      const resetPwdBtn = row.querySelector('[data-action="reset-password"]');
      if (resetPwdBtn) {
        resetPwdBtn.addEventListener("click", async () => {
          errEl.textContent = "";
          const next = await promptResetPassword(username);
          if (!next) return;
          try {
            await api.post(`/api/auth/users/${encodeURIComponent(username)}/reset-password`, {
              new_password: String(next),
            });
          } catch (err) {
            errEl.textContent = String((err && err.message) || "重置密码失败");
          }
        });
      }

      rowsEl.appendChild(row);
    }
  }

  createBtn.addEventListener("click", async () => {
    errEl.textContent = "";
    const username = String(createName.value || "").trim();
    const password = String(createPassword.value || "");
    const role = String(createRole.value || "member").trim();
    const tenant = String(createTenant.value || "").trim();
    if (!username) {
      errEl.textContent = "请输入用户名";
      return;
    }
    if (password.length < 6) {
      errEl.textContent = "密码至少 6 位";
      return;
    }
    const payload = { username, password, role };
    if (tenant) payload.tenant_id = tenant;
    try {
      await api.post("/api/auth/users", payload);
      createPassword.value = "";
      await loadUsers();
      await renderUserRows();
    } catch (err) {
      errEl.textContent = String((err && err.message) || "创建用户失败");
    }
  });

  reloadBtn.addEventListener("click", async () => {
    errEl.textContent = "";
    try {
      await loadUsers();
      await renderUserRows();
    } catch (err) {
      errEl.textContent = String((err && err.message) || "刷新用户失败");
    }
  });

  changePwdBtn.addEventListener("click", async () => {
    errEl.textContent = "";
    const oldPassword = String(oldPwd.value || "");
    const nextPassword = String(newPwd.value || "");
    const confirm = String(newPwdConfirm.value || "");
    if (!oldPassword || !nextPassword) {
      errEl.textContent = "请输入旧密码和新密码";
      return;
    }
    if (nextPassword.length < 6) {
      errEl.textContent = "新密码至少 6 位";
      return;
    }
    if (nextPassword !== confirm) {
      errEl.textContent = "两次输入的新密码不一致";
      return;
    }
    try {
      await api.post("/api/auth/change-password", {
        old_password: oldPassword,
        new_password: nextPassword,
      });
      oldPwd.value = "";
      newPwd.value = "";
      newPwdConfirm.value = "";
    } catch (err) {
      errEl.textContent = String((err && err.message) || "修改密码失败");
    }
  });

  await renderUserRows();
  panel.innerHTML = "";
  panel.appendChild(wrap);
}

async function renderSecurity(container) {
  const panel = container.querySelector("#settingsPanel");
  panel.innerHTML = `<div class="panel"><h3 style="margin-top:0">安全</h3><div class="muted">加载中...</div></div>`;

  const wrap = el(`<div class="panel">
      <h3 style="margin-top:0">安全</h3>
      <div class="muted" style="margin-top:6px">仅 Owner 可见（观测面板）</div>

      <div style="margin-top: 16px">
        <div class="row" style="justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;">
          <h4 style="margin: 0">登录锁定状态</h4>
          <div class="row" style="gap: 8px; flex-wrap: wrap;">
            <input id="securityLockUsername" type="text" placeholder="用户名（精确）" style="min-width: 150px" />
            <input id="securityLockIp" type="text" placeholder="IP（精确）" style="min-width: 130px" />
            <select id="securityLockScope">
              <option value="">全部范围</option>
              <option value="user_ip">用户+IP</option>
              <option value="ip">IP</option>
            </select>
            <input id="securityLockReason" type="text" placeholder="解锁原因（必填）" style="min-width: 220px" />
            <select id="securityLockView">
              <option value="active" selected>仅显示锁定</option>
              <option value="all">全部主体</option>
            </select>
            <select id="securityLockLimit">
              <option value="50">50 行</option>
              <option value="100" selected>100 行</option>
              <option value="200">200 行</option>
            </select>
            <button class="btn" type="button" id="securityLockBatchUnlockBtn">解锁选中</button>
            <button class="btn secondary" type="button" id="securityLockRefreshBtn">刷新</button>
          </div>
        </div>
        <div class="muted" id="securityLockSummary" style="margin-top: 6px"></div>
        <div class="error" id="securityLockError" style="margin-top: 8px"></div>
        <table class="table" style="margin-top:10px">
          <thead>
            <tr><th><input id="securityLockSelectAll" type="checkbox" /></th><th>范围</th><th>主体</th><th>状态</th><th>重试</th><th>失败次数</th><th>最近失败</th><th>锁定到</th><th></th></tr>
          </thead>
          <tbody id="securityLockRows"></tbody>
        </table>
        <div class="muted" id="securityLockEmpty" style="margin-top: 8px"></div>
      </div>

      <div style="margin-top: 20px">
        <h4 style="margin: 0">安全事件</h4>
        <div class="muted" style="margin-top:6px">最新审计事件</div>
        <div class="row" style="margin-top:8px; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;">
          <div class="muted" id="securityRetentionSummary"></div>
          <button class="btn secondary" type="button" id="securityRetentionRunBtn">立即运行清理</button>
        </div>
        <div class="row" style="margin-top: 8px; gap: 8px; flex-wrap: wrap;">
          <button class="btn secondary" type="button" id="securityPresetSessionAllBtn">会话事件</button>
          <button class="btn secondary" type="button" id="securityPresetSessionRevokeBtn">会话撤销</button>
          <button class="btn secondary" type="button" id="securityPresetSessionRevokeAllBtn">会话批量撤销</button>
          <button class="btn secondary" type="button" id="securityExportSessionBtn">导出会话 CSV</button>
        </div>
        <div class="row" style="margin-top: 12px; gap: 8px; flex-wrap: wrap;">
          <input id="securityEventFilter" type="text" placeholder="事件包含..." style="min-width: 180px" />
          <input id="securityActorFilter" type="text" placeholder="操作者（精确）" style="min-width: 140px" />
          <select id="securityStatusFilter">
            <option value="">任意状态</option>
            <option value="succeeded">成功</option>
            <option value="failed">失败</option>
            <option value="blocked">被拦截</option>
          </select>
          <select id="securityMetaModeFilter">
            <option value="">任意模式</option>
            <option value="single">单条</option>
            <option value="batch">批量</option>
          </select>
          <input id="securityMetaUsernameFilter" type="text" placeholder="用户名（精确）" style="min-width: 160px" />
          <input id="securityMetaReasonFilter" type="text" placeholder="原因包含..." style="min-width: 180px" />
          <input id="securityMetaSubjectFilter" type="text" placeholder="subject_key（精确）" style="min-width: 180px" />
          <select id="securityLimitFilter">
            <option value="50">50 行</option>
            <option value="100" selected>100 行</option>
            <option value="200">200 行</option>
          </select>
          <button class="btn" type="button" id="securityApplyBtn">应用</button>
          <button class="btn secondary" type="button" id="securityExportBtn">导出 CSV</button>
          <button class="btn secondary" type="button" id="securityMoreBtn">加载更早</button>
        </div>
        <div class="error" id="securityError" style="margin-top: 8px"></div>
        <table class="table" style="margin-top:10px">
          <thead>
            <tr><th>时间</th><th>事件</th><th>状态</th><th>操作者</th><th>IP</th><th>元数据</th></tr>
          </thead>
          <tbody id="securityRows"></tbody>
        </table>
        <div class="muted" id="securityEmpty" style="margin-top: 8px"></div>
      </div>
    </div>`);

  const lockRowsEl = wrap.querySelector("#securityLockRows");
  const lockSummaryEl = wrap.querySelector("#securityLockSummary");
  const lockErrEl = wrap.querySelector("#securityLockError");
  const lockEmptyEl = wrap.querySelector("#securityLockEmpty");
  const lockUsernameInput = wrap.querySelector("#securityLockUsername");
  const lockIpInput = wrap.querySelector("#securityLockIp");
  const lockScopeSelect = wrap.querySelector("#securityLockScope");
  const lockReasonInput = wrap.querySelector("#securityLockReason");
  const lockViewSelect = wrap.querySelector("#securityLockView");
  const lockLimitSelect = wrap.querySelector("#securityLockLimit");
  const lockBatchUnlockBtn = wrap.querySelector("#securityLockBatchUnlockBtn");
  const lockRefreshBtn = wrap.querySelector("#securityLockRefreshBtn");
  const lockSelectAll = wrap.querySelector("#securityLockSelectAll");

  const tbody = wrap.querySelector("#securityRows");
  const errEl = wrap.querySelector("#securityError");
  const emptyEl = wrap.querySelector("#securityEmpty");
  const eventInput = wrap.querySelector("#securityEventFilter");
  const actorInput = wrap.querySelector("#securityActorFilter");
  const statusSelect = wrap.querySelector("#securityStatusFilter");
  const metaModeSelect = wrap.querySelector("#securityMetaModeFilter");
  const metaUsernameInput = wrap.querySelector("#securityMetaUsernameFilter");
  const metaReasonInput = wrap.querySelector("#securityMetaReasonFilter");
  const metaSubjectInput = wrap.querySelector("#securityMetaSubjectFilter");
  const limitSelect = wrap.querySelector("#securityLimitFilter");
  const retentionSummaryEl = wrap.querySelector("#securityRetentionSummary");
  const retentionRunBtn = wrap.querySelector("#securityRetentionRunBtn");
  const sessionPresetAllBtn = wrap.querySelector("#securityPresetSessionAllBtn");
  const sessionPresetRevokeBtn = wrap.querySelector("#securityPresetSessionRevokeBtn");
  const sessionPresetRevokeAllBtn = wrap.querySelector("#securityPresetSessionRevokeAllBtn");
  const sessionExportBtn = wrap.querySelector("#securityExportSessionBtn");
  const applyBtn = wrap.querySelector("#securityApplyBtn");
  const exportBtn = wrap.querySelector("#securityExportBtn");
  const moreBtn = wrap.querySelector("#securityMoreBtn");

  let lockRows = [];
  let selectedLockKeys = new Set();
  let locksLoading = false;
  let rows = [];
  let nextBeforeTs = "";
  let eventsLoading = false;
  let retentionLoading = false;

  function formatRetry(seconds) {
    const total = Math.max(0, Number(seconds || 0));
    if (!total) return "-";
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    if (!secs) return `${mins}m`;
    return `${mins}m ${secs}s`;
  }

  function setLockControlsState(disabled) {
    lockRefreshBtn.disabled = disabled;
    lockUsernameInput.disabled = disabled;
    lockIpInput.disabled = disabled;
    lockScopeSelect.disabled = disabled;
    lockReasonInput.disabled = disabled;
    lockViewSelect.disabled = disabled;
    lockLimitSelect.disabled = disabled;
    lockBatchUnlockBtn.disabled = disabled || selectedLockKeys.size === 0;
    lockSelectAll.disabled = disabled || lockRows.length === 0;
  }

  function normalizeReason() {
    return String(lockReasonInput.value || "").trim();
  }

  function selectedLockedRows() {
    return (lockRows || []).filter((item) => item && item.locked && selectedLockKeys.has(String(item.subject_key || "")));
  }

  function syncLockSelectionUi() {
    const selectedRows = selectedLockedRows();
    lockBatchUnlockBtn.disabled = locksLoading || selectedRows.length === 0;
    lockBatchUnlockBtn.textContent =
      selectedRows.length > 0 ? `解锁选中（${selectedRows.length}）` : "解锁选中";
    const totalLocked = (lockRows || []).filter((item) => Boolean(item && item.locked)).length;
    lockSelectAll.checked = totalLocked > 0 && selectedRows.length === totalLocked;
    lockSelectAll.indeterminate = selectedRows.length > 0 && selectedRows.length < totalLocked;
    lockSelectAll.disabled = locksLoading || totalLocked === 0;
  }

  function pruneSelection() {
    const validKeys = new Set(
      (lockRows || [])
        .filter((item) => Boolean(item && item.locked))
        .map((item) => String(item.subject_key || ""))
        .filter(Boolean)
    );
    selectedLockKeys = new Set([...selectedLockKeys].filter((key) => validKeys.has(key)));
    syncLockSelectionUi();
  }

  function requireUnlockReason() {
    const reason = normalizeReason();
    if (!reason) {
      lockErrEl.textContent = "请填写解锁原因。";
      lockReasonInput.focus();
      return null;
    }
    return reason;
  }

  function focusUnlockAudits({ subjectKey = "", reason = "", mode = "" } = {}) {
    eventInput.value = "security.login_lock.unlock";
    statusSelect.value = "succeeded";
    if (subjectKey) metaSubjectInput.value = String(subjectKey || "");
    if (reason) metaReasonInput.value = String(reason || "");
    if (mode) metaModeSelect.value = String(mode || "");
    nextBeforeTs = "";
    moreBtn.textContent = "加载更早";
  }

  async function unlockSubject(subjectKey) {
    if (!subjectKey || locksLoading) return;
    const reason = requireUnlockReason();
    if (!reason) return;
    if (!window.confirm("确定解锁该主体吗？")) return;
    lockErrEl.textContent = "";
    try {
      const result = await api.post("/api/security/login-locks/unlock", {
        subject_key: subjectKey,
        reason,
      });
      if (!result || !result.cleared) {
        lockErrEl.textContent = "该主体已处于解锁状态。";
      }
      selectedLockKeys.delete(String(subjectKey || ""));
      focusUnlockAudits({ subjectKey, reason, mode: "single" });
      await Promise.all([loadLockSnapshot(), loadEvents({ append: false })]);
    } catch (err) {
      lockErrEl.textContent = String((err && err.message) || "解锁失败");
    }
  }

  async function unlockSelectedSubjects() {
    if (locksLoading) return;
    const reason = requireUnlockReason();
    if (!reason) return;

    const selectedRows = selectedLockedRows();
    if (!selectedRows.length) {
      lockErrEl.textContent = "未选择任何锁定主体。";
      return;
    }
    if (!window.confirm(`确定解锁已选择的 ${selectedRows.length} 个主体吗？`)) return;

    lockErrEl.textContent = "";
    try {
      const payload = {
        subject_keys: selectedRows.map((item) => String(item.subject_key || "")).filter(Boolean),
        reason,
      };
      const result = await api.post("/api/security/login-locks/unlock-batch", payload);
      const notFound = Number((result && result.not_found) || 0);
      if (notFound > 0) {
        lockErrEl.textContent = `有 ${notFound} 个选中的主体已处于解锁状态。`;
      }
      selectedLockKeys.clear();
      focusUnlockAudits({ reason, mode: "batch" });
      await Promise.all([loadLockSnapshot(), loadEvents({ append: false })]);
    } catch (err) {
      lockErrEl.textContent = String((err && err.message) || "批量解锁失败");
    }
  }

  function renderLockRows() {
    lockRowsEl.innerHTML = "";
    for (const item of lockRows || []) {
      let subject = "-";
      if (item.scope === "user_ip") {
        const username = item.username || "-";
        const ip = item.ip || "-";
        subject = `${username}@${ip}`;
      } else if (item.scope === "ip") {
        subject = item.ip || "-";
      } else {
        subject = item.subject_key || "-";
      }

      const subjectKey = String(item.subject_key || "");
      const isLocked = Boolean(item.locked);
      const checked = isLocked && selectedLockKeys.has(subjectKey);
      const scopeLabel =
        item.scope === "user_ip" ? "用户+IP" : item.scope === "ip" ? "IP" : String(item.scope || "");
      const row = el(`<tr>
          <td><input type="checkbox" data-action="select-lock"${checked ? " checked" : ""}${isLocked ? "" : " disabled"} /></td>
          <td>${escapeHtml(scopeLabel)}</td>
          <td class="code">${escapeHtml(subject)}</td>
          <td>${isLocked ? '<span class="badge">锁定</span>' : "监控中"}</td>
          <td>${escapeHtml(formatRetry(item.retry_after_s || 0))}</td>
          <td>${escapeHtml(String(item.failure_count || 0))}</td>
          <td>${escapeHtml(formatDateTime(item.last_failure_at || ""))}</td>
          <td>${escapeHtml(formatDateTime(item.locked_until || ""))}</td>
          <td><button class="btn secondary" type="button" data-action="unlock"${isLocked ? "" : " disabled"}>解锁</button></td>
        </tr>`);
      row.querySelector('[data-action="select-lock"]').addEventListener("change", (e) => {
        const checkedNow = Boolean(e.target && e.target.checked);
        if (checkedNow) selectedLockKeys.add(subjectKey);
        else selectedLockKeys.delete(subjectKey);
        syncLockSelectionUi();
      });
      row.querySelector('[data-action="unlock"]').addEventListener("click", () => {
        unlockSubject(subjectKey).catch(() => {});
      });
      lockRowsEl.appendChild(row);
    }
    pruneSelection();
    lockEmptyEl.textContent = lockRows.length ? "" : "当前筛选条件下未找到锁定主体。";
  }

  async function loadLockSnapshot() {
    if (locksLoading) return;
    locksLoading = true;
    setLockControlsState(true);
    lockErrEl.textContent = "";
    lockEmptyEl.textContent = "";

    const includeUnlocked = String(lockViewSelect.value || "active") === "all";
    const limit = Math.max(1, Math.min(500, Number(lockLimitSelect.value || 100)));
    const username = String(lockUsernameInput.value || "").trim();
    const ip = String(lockIpInput.value || "").trim();
    const scope = String(lockScopeSelect.value || "").trim();
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (includeUnlocked) params.set("include_unlocked", "true");
    if (username) params.set("username", username);
    if (ip) params.set("ip", ip);
    if (scope) params.set("scope", scope);

    try {
      const snapshot = await api.get(`/api/security/login-locks?${params.toString()}`);
      const items = Array.isArray(snapshot && snapshot.items) ? snapshot.items : [];
      lockRows = items;
      renderLockRows();
      const activeCount = Number(snapshot && snapshot.active_lock_count) || 0;
      const subjectCount = Number(snapshot && snapshot.subject_count) || 0;
      const generatedAt = formatDateTime((snapshot && snapshot.generated_at) || "");
      lockSummaryEl.textContent = `当前锁定：${activeCount}；跟踪主体：${subjectCount}${generatedAt ? `；更新时间：${generatedAt}` : ""}`;
    } catch (err) {
      const msg = String((err && err.message) || "加载失败");
      if (msg.toLowerCase().includes("insufficient role")) {
        lockErrEl.textContent = "登录锁定状态仅对 Owner 角色开放。";
      } else {
        lockErrEl.textContent = msg;
      }
    } finally {
      locksLoading = false;
      setLockControlsState(false);
    }
  }

  function renderRows() {
    tbody.innerHTML = "";
    for (const item of rows || []) {
      const row = el(`<tr>
          <td>${escapeHtml(formatDateTime(item.ts || ""))}</td>
          <td><span class="badge">${escapeHtml(item.event || "")}</span></td>
          <td>${escapeHtml(item.status || "")}</td>
          <td>${escapeHtml(item.actor || "-")}</td>
          <td>${escapeHtml(item.ip || "-")}</td>
          <td class="code">${escapeHtml(JSON.stringify(item.metadata || {}))}</td>
        </tr>`);
      tbody.appendChild(row);
    }
    emptyEl.textContent = rows.length ? "" : "当前筛选条件下未找到审计事件。";
  }

  function buildEventParams({ append = false } = {}) {
    const limit = Math.max(1, Math.min(500, Number(limitSelect.value || 100)));
    const params = new URLSearchParams();
    params.set("limit", String(limit));

    const eventFilter = String(eventInput.value || "").trim();
    const actorFilter = String(actorInput.value || "").trim();
    const statusFilter = String(statusSelect.value || "").trim();
    const metaModeFilter = String(metaModeSelect.value || "").trim();
    const metaUsernameFilter = String(metaUsernameInput.value || "").trim();
    const metaReasonFilter = String(metaReasonInput.value || "").trim();
    const metaSubjectFilter = String(metaSubjectInput.value || "").trim();
    if (eventFilter) params.set("event", eventFilter);
    if (actorFilter) params.set("actor", actorFilter);
    if (statusFilter) params.set("status", statusFilter);
    if (metaModeFilter) params.set("meta_mode", metaModeFilter);
    if (metaUsernameFilter) params.set("meta_username", metaUsernameFilter);
    if (metaReasonFilter) params.set("meta_reason", metaReasonFilter);
    if (metaSubjectFilter) params.set("meta_subject_key", metaSubjectFilter);
    if (append && nextBeforeTs) params.set("before", nextBeforeTs);
    return { params, limit };
  }

  function setRetentionControlsState(disabled) {
    retentionRunBtn.disabled = disabled;
  }

  function setButtonsState(disabled) {
    applyBtn.disabled = disabled;
    exportBtn.disabled = disabled;
    sessionPresetAllBtn.disabled = disabled;
    sessionPresetRevokeBtn.disabled = disabled;
    sessionPresetRevokeAllBtn.disabled = disabled;
    sessionExportBtn.disabled = disabled;
    moreBtn.disabled = disabled || !nextBeforeTs;
  }

  function triggerCsvDownload(text) {
    const csvText = String(text || "");
    const stamp = new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
    const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `audit-events-${stamp}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  function resetEventPagination() {
    nextBeforeTs = "";
    moreBtn.textContent = "加载更早";
  }

  function applyEventPreset({
    event = "",
    status = "",
    metaMode = "",
    metaUsername = "",
    metaReason = "",
    metaSubject = "",
  } = {}) {
    eventInput.value = String(event || "");
    statusSelect.value = String(status || "");
    metaModeSelect.value = String(metaMode || "");
    metaUsernameInput.value = String(metaUsername || "");
    metaReasonInput.value = String(metaReason || "");
    metaSubjectInput.value = String(metaSubject || "");
  }

  function buildSessionEventParams(eventText = "auth.user.session.", mode = "") {
    const limit = Math.max(1, Math.min(500, Number(limitSelect.value || 100)));
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    params.set("event", String(eventText || "auth.user.session."));
    params.set("status", "succeeded");
    if (mode) params.set("meta_mode", String(mode || ""));
    const actorFilter = String(actorInput.value || "").trim();
    const metaUsername = String(metaUsernameInput.value || "").trim();
    if (actorFilter) params.set("actor", actorFilter);
    if (metaUsername) params.set("meta_username", metaUsername);
    return params;
  }

  async function exportEventsCsvWithParams(params) {
    const token = getToken();
    const response = await fetch(`/api/audit/events/export?${params.toString()}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (response.status === 401) {
      window.location.hash = "#/login";
      throw new Error("未授权");
    }
    const payloadText = await response.text();
    if (!response.ok) {
      let detail = "";
      try {
        const parsed = JSON.parse(payloadText || "{}");
        detail = String(parsed.detail || "");
      } catch {
        detail = "";
      }
      throw new Error(detail || response.statusText || "导出失败");
    }
    triggerCsvDownload(payloadText);
  }

  async function loadRetentionStatus() {
    if (retentionLoading) return;
    retentionLoading = true;
    setRetentionControlsState(true);
    try {
      const statusInfo = await api.get("/api/audit/retention");
      const enabled = Boolean(statusInfo && statusInfo.enabled);
      const days = Number((statusInfo && statusInfo.retention_days) || 0);
      const lastRunAt = formatDateTime((statusInfo && statusInfo.last_run_at) || "");
      const lastPruned = Number((statusInfo && statusInfo.pruned_lines) || 0);
      retentionSummaryEl.textContent = enabled
        ? `清理策略：保留 ${days} 天${lastRunAt ? `；上次运行：${lastRunAt}` : ""}${lastRunAt ? `；清理行数：${lastPruned}` : ""}`
        : "清理策略已禁用。";
    } catch (err) {
      const msg = String((err && err.message) || "加载清理状态失败");
      if (msg.toLowerCase().includes("insufficient role")) {
        retentionSummaryEl.textContent = "清理状态仅对 Owner 角色开放。";
      } else {
        retentionSummaryEl.textContent = msg;
      }
    } finally {
      retentionLoading = false;
      setRetentionControlsState(false);
    }
  }

  async function runRetentionNow() {
    if (retentionLoading) return;
    if (!window.confirm("现在执行审计日志清理吗？")) return;
    errEl.textContent = "";
    setRetentionControlsState(true);
    try {
      retentionLoading = true;
      await api.post("/api/audit/retention/run", {});
      retentionLoading = false;
      await Promise.all([loadRetentionStatus(), loadEvents({ append: false })]);
    } catch (err) {
      errEl.textContent = String((err && err.message) || "执行清理失败");
    } finally {
      retentionLoading = false;
      setRetentionControlsState(false);
    }
  }

  async function exportEventsCsv() {
    if (eventsLoading) return;
    errEl.textContent = "";
    exportBtn.disabled = true;
    try {
      const { params } = buildEventParams({ append: false });
      await exportEventsCsvWithParams(params);
    } catch (err) {
      errEl.textContent = String((err && err.message) || "导出失败");
    } finally {
      exportBtn.disabled = false;
    }
  }

  async function exportSessionEventsCsv(eventText = "auth.user.session.", mode = "") {
    if (eventsLoading) return;
    errEl.textContent = "";
    sessionExportBtn.disabled = true;
    try {
      const params = buildSessionEventParams(eventText, mode);
      await exportEventsCsvWithParams(params);
    } catch (err) {
      errEl.textContent = String((err && err.message) || "导出失败");
    } finally {
      sessionExportBtn.disabled = false;
    }
  }

  async function focusSessionEvents(eventText = "auth.user.session.", mode = "") {
    applyEventPreset({ event: eventText, status: "succeeded", metaMode: mode });
    resetEventPagination();
    await loadEvents({ append: false });
  }

  async function loadEvents({ append = false } = {}) {
    if (eventsLoading) return;
    eventsLoading = true;
    setButtonsState(true);
    errEl.textContent = "";
    emptyEl.textContent = "";

    const { params, limit } = buildEventParams({ append });

    try {
      const result = await api.get(`/api/audit/events?${params.toString()}`);
      const pageRows = Array.isArray(result) ? result : [];
      rows = append ? rows.concat(pageRows) : pageRows;
      nextBeforeTs = rows.length ? String(rows[rows.length - 1].ts || "") : "";
      renderRows();
      const hasMore = pageRows.length >= limit && Boolean(nextBeforeTs);
      moreBtn.disabled = !hasMore;
      if (!hasMore) {
        moreBtn.textContent = "没有更多数据";
      } else {
        moreBtn.textContent = "加载更早";
      }
    } catch (err) {
      const msg = String((err && err.message) || "加载失败");
      if (msg.toLowerCase().includes("insufficient role")) {
        errEl.textContent = "安全事件仅对 Owner 角色开放。";
      } else {
        errEl.textContent = msg;
      }
    } finally {
      eventsLoading = false;
      setButtonsState(false);
    }
  }

  lockRefreshBtn.addEventListener("click", () => {
    loadLockSnapshot().catch(() => {});
  });
  lockUsernameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadLockSnapshot().catch(() => {});
  });
  lockIpInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadLockSnapshot().catch(() => {});
  });
  lockReasonInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") unlockSelectedSubjects().catch(() => {});
  });
  lockScopeSelect.addEventListener("change", () => {
    loadLockSnapshot().catch(() => {});
  });
  lockViewSelect.addEventListener("change", () => {
    loadLockSnapshot().catch(() => {});
  });
  lockLimitSelect.addEventListener("change", () => {
    loadLockSnapshot().catch(() => {});
  });
  lockBatchUnlockBtn.addEventListener("click", () => {
    unlockSelectedSubjects().catch(() => {});
  });
  lockSelectAll.addEventListener("change", () => {
    const shouldSelect = Boolean(lockSelectAll.checked);
    for (const item of lockRows || []) {
      if (!item || !item.locked) continue;
      const key = String(item.subject_key || "");
      if (!key) continue;
      if (shouldSelect) selectedLockKeys.add(key);
      else selectedLockKeys.delete(key);
    }
    renderLockRows();
  });
  eventInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      nextBeforeTs = "";
      moreBtn.textContent = "加载更早";
      loadEvents({ append: false }).catch(() => {});
    }
  });
  actorInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      nextBeforeTs = "";
      moreBtn.textContent = "加载更早";
      loadEvents({ append: false }).catch(() => {});
    }
  });
  metaReasonInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      nextBeforeTs = "";
      moreBtn.textContent = "加载更早";
      loadEvents({ append: false }).catch(() => {});
    }
  });
  metaUsernameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      nextBeforeTs = "";
      moreBtn.textContent = "加载更早";
      loadEvents({ append: false }).catch(() => {});
    }
  });
  metaSubjectInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      nextBeforeTs = "";
      moreBtn.textContent = "加载更早";
      loadEvents({ append: false }).catch(() => {});
    }
  });
  applyBtn.addEventListener("click", () => {
    resetEventPagination();
    loadEvents({ append: false }).catch(() => {});
  });
  sessionPresetAllBtn.addEventListener("click", () => {
    focusSessionEvents("auth.user.session.").catch(() => {});
  });
  sessionPresetRevokeBtn.addEventListener("click", () => {
    focusSessionEvents("auth.user.session.revoke", "single").catch(() => {});
  });
  sessionPresetRevokeAllBtn.addEventListener("click", () => {
    focusSessionEvents("auth.user.session.revoke_all", "batch").catch(() => {});
  });
  sessionExportBtn.addEventListener("click", () => {
    exportSessionEventsCsv("auth.user.session.").catch(() => {});
  });
  exportBtn.addEventListener("click", () => {
    exportEventsCsv().catch(() => {});
  });
  retentionRunBtn.addEventListener("click", () => {
    runRetentionNow().catch(() => {});
  });
  moreBtn.addEventListener("click", () => {
    loadEvents({ append: true }).catch(() => {});
  });

  await Promise.all([loadLockSnapshot(), loadEvents({ append: false }), loadRetentionStatus()]);
  panel.innerHTML = "";
  panel.appendChild(wrap);
}

export async function renderSettings(container) {
  container.innerHTML = `
    <div class="tabs">
      <div class="tab active" data-tab="providers">模型服务</div>
      <div class="tab" data-tab="channels">渠道</div>
      <div class="tab" data-tab="beta">封闭 Beta</div>
      <div class="tab" data-tab="users">用户</div>
      <div class="tab" data-tab="security">安全</div>
    </div>
    <div id="settingsPanel"></div>
  `;

  const tabs = Array.from(container.querySelectorAll(".tab"));

  async function select(tabName) {
    for (const t of tabs) t.classList.toggle("active", t.dataset.tab === tabName);
    if (tabName === "providers") await renderProviders(container);
    if (tabName === "channels") await renderChannels(container);
    if (tabName === "beta") await renderBetaAccess(container);
    if (tabName === "users") await renderUsers(container);
    if (tabName === "security") await renderSecurity(container);
  }

  tabs.forEach((t) => {
    t.addEventListener("click", () => {
      select(t.dataset.tab).catch(() => {});
    });
  });

  await select("providers");
  return () => {};
}
