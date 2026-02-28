import { api, setAuthTokens } from "../api.js";

export async function renderLogin(container) {
  container.innerHTML = `
    <div class="panel">
      <h2>Login</h2>
      <p class="muted">Use the admin credentials configured on the server.</p>
      <form id="loginForm" class="row" style="gap: 16px">
        <div class="col">
          <label>Username</label>
          <input id="username" type="text" autocomplete="username" placeholder="admin" />
        </div>
        <div class="col">
          <label>Password</label>
          <input id="password" type="password" autocomplete="current-password" />
        </div>
        <div class="col">
          <label>Invite Code (optional)</label>
          <input id="inviteCode" type="text" autocomplete="one-time-code" placeholder="For closed beta access" />
        </div>
        <div class="row" style="width: 100%; justify-content: flex-end">
          <button class="btn" type="submit">Login</button>
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
      errEl.textContent = err.message || "Login failed";
    }
  });

  userEl.focus();
  return () => {};
}

