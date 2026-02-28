import { api, clearToken, getToken } from "./api.js";
import { renderLogin } from "./pages/login.js";
import { renderChat } from "./pages/chat.js";
import { renderSettings } from "./pages/settings.js";
import { renderSkills } from "./pages/skills.js";
import { renderOps } from "./pages/ops.js";

const appEl = document.getElementById("app");
const navEl = document.getElementById("nav");
const logoutBtn = document.getElementById("logoutBtn");
const globalNoticeWrap = document.getElementById("globalNoticeWrap");
const globalNoticeEl = document.getElementById("globalNotice");

let cleanupFn = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fallbackCopyTextToClipboard(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.left = "-1000px";
  document.body.appendChild(ta);
  ta.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

async function copyTextToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return fallbackCopyTextToClipboard(text);
  }
}

async function fetchReadyPayload() {
  const res = await fetch("/api/ready", { cache: "no-store" });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { status: "unknown", warnings: [text] };
    }
  }
  return { res, data };
}

async function refreshGlobalReadyNotice() {
  if (!globalNoticeWrap || !globalNoticeEl) return;

  let payload = null;
  let degraded = false;
  let fetchError = "";

  try {
    const { res, data } = await fetchReadyPayload();
    payload = data || {};
    degraded = !res.ok || String(payload.status || "") !== "ready";
  } catch (e) {
    fetchError = String((e && e.message) || e || "Failed to load readiness");
    degraded = true;
  }

  const warnings = Array.isArray((payload || {}).warnings) ? payload.warnings : [];
  const show = degraded || warnings.length > 0 || Boolean(fetchError);
  globalNoticeWrap.classList.toggle("hidden", !show);
  if (!show) return;

  globalNoticeEl.className = "notice";
  globalNoticeEl.classList.add(degraded ? "warn" : "ok");

  const title = degraded ? "Service degraded" : "Service ready";
  const version = payload && payload.version ? `v${payload.version}` : "";
  const diag = {
    at: new Date().toISOString(),
    url: window.location.href,
    ready: payload,
    error: fetchError || null,
  };
  const diagText = JSON.stringify(diag, null, 2);

  const warningsHtml = warnings.length
    ? `<ul style="margin: 10px 0 0 18px">${warnings
        .map((w) => `<li>${escapeHtml(w)}</li>`)
        .join("")}</ul>`
    : "";
  const errorHtml = fetchError ? `<div class="muted">${escapeHtml(fetchError)}</div>` : "";

  globalNoticeEl.innerHTML = `
    <div class="row" style="justify-content: space-between; gap: 12px">
      <div>
        <div style="font-weight: 700">${escapeHtml(title)} ${escapeHtml(version)}</div>
        ${errorHtml}
      </div>
      <div class="row" style="gap: 8px">
        <button id="readyRefreshBtn" class="btn secondary" type="button">Refresh</button>
        <button id="readyCopyBtn" class="btn secondary" type="button">Copy diagnostics</button>
      </div>
    </div>
    ${warningsHtml}
  `;

  const refreshBtn = globalNoticeEl.querySelector("#readyRefreshBtn");
  const copyBtn = globalNoticeEl.querySelector("#readyCopyBtn");
  refreshBtn.addEventListener("click", () => {
    refreshGlobalReadyNotice().catch(() => {});
  });
  copyBtn.addEventListener("click", async () => {
    const ok = await copyTextToClipboard(diagText);
    copyBtn.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(() => {
      copyBtn.textContent = "Copy diagnostics";
    }, 1200);
  });
}

function setActiveNav(route) {
  for (const a of navEl.querySelectorAll("a[data-route]")) {
    a.classList.toggle("active", a.getAttribute("data-route") === route);
  }
}

function showNav(show) {
  navEl.classList.toggle("hidden", !show);
}

function routeFromHash() {
  const raw = window.location.hash || "#/login";
  if (!raw.startsWith("#/")) return "#/login";
  return raw;
}

async function renderRoute() {
  refreshGlobalReadyNotice().catch(() => {});

  if (cleanupFn) {
    try {
      cleanupFn();
    } catch {
      // ignore
    }
    cleanupFn = null;
  }

  const token = getToken();
  const route = routeFromHash();

  if (!token && route !== "#/login") {
    window.location.hash = "#/login";
    return;
  }

  if (token && route === "#/login") {
    window.location.hash = "#/chat";
    return;
  }

  showNav(Boolean(token));
  setActiveNav(route);

  if (route === "#/login") {
    cleanupFn = await renderLogin(appEl);
    return;
  }
  if (route === "#/chat") {
    cleanupFn = await renderChat(appEl);
    return;
  }
  if (route === "#/settings") {
    cleanupFn = await renderSettings(appEl);
    return;
  }
  if (route === "#/skills") {
    cleanupFn = await renderSkills(appEl);
    return;
  }
  if (route === "#/ops") {
    cleanupFn = await renderOps(appEl);
    return;
  }

  window.location.hash = "#/chat";
}

logoutBtn.addEventListener("click", async () => {
  try {
    await api.post("/api/auth/logout", { revoke_all: true });
  } catch {
    // ignore network/auth failures on best-effort logout
  } finally {
    clearToken();
    window.location.hash = "#/login";
  }
});

window.addEventListener("hashchange", () => {
  renderRoute().catch((e) => {
    appEl.innerHTML = `<div class="panel"><div class="error">${e.message}</div></div>`;
  });
});

renderRoute().catch((e) => {
  appEl.innerHTML = `<div class="panel"><div class="error">${e.message}</div></div>`;
});

