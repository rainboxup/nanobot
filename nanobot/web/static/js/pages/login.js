import { api, setAuthTokens } from "../api.js";

export async function renderLogin(container) {
  container.innerHTML = `
    <div class="panel">
      <h2>登录</h2>
      <p class="muted">使用服务器上配置的管理员账号密码。</p>
      <form id="loginForm" class="row" style="gap: 16px">
        <div class="col">
          <label>用户名</label>
          <input id="username" type="text" autocomplete="username" placeholder="admin" />
        </div>
        <div class="col">
          <label>密码</label>
          <input id="password" type="password" autocomplete="current-password" />
        </div>
        <div class="col">
          <label>邀请码（可选）</label>
          <input id="inviteCode" type="text" autocomplete="one-time-code" placeholder="用于封闭 Beta 访问" />
        </div>
        <div class="row" style="width: 100%; justify-content: flex-end">
          <button class="btn" type="submit">登录</button>
        </div>
      </form>
      <div id="loginError" class="error" style="margin-top: 10px"></div>
    </div>
  `;

  const form = container.querySelector("#loginForm");
  const errEl = container.querySelector("#loginError");
  const userEl = container.querySelector("#username");
  const passEl = container.querySelector("#password");
  const inviteEl = container.querySelector("#inviteCode");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.textContent = "";
    try {
      const payload = {
        username: (userEl.value || "").trim() || "admin",
        password: passEl.value || "",
      };
      const inviteCode = (inviteEl.value || "").trim();
      if (inviteCode) payload.invite_code = inviteCode;
      const res = await api.post("/api/auth/login", payload);
      setAuthTokens(res.access_token || res.token, res.refresh_token || "");
      window.location.hash = "#/chat";
    } catch (err) {
      errEl.textContent = err.message || "登录失败";
    }
  });

  userEl.focus();
  return () => {};
}

