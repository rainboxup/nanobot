import { api, formatTime } from "../api.js";
import { showToast } from "../toast.js";

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

function formatPercent(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0%";
  return `${(n * 100).toFixed(1)}%`;
}

function statusLabel(value) {
  const v = String(value || "").toLowerCase();
  if (v === "ready") return "正常";
  if (v === "degraded") return "异常";
  if (v === "unknown") return "未知";
  return String(value || "") || "未知";
}

export async function renderOps(container) {
  const wrap = el(`
    <div class="panel">
      <div class="row" style="justify-content: space-between">
        <div>
          <h2 style="margin:0">运维</h2>
          <div class="muted">运行时快照，用于排查问题与支持。</div>
        </div>
        <div class="row" style="gap: 8px">
          <button id="opsRefreshBtn" class="btn secondary" type="button">刷新</button>
          <button id="opsCopyBtn" class="btn secondary" type="button">复制 JSON</button>
        </div>
      </div>
      <div id="opsBody" style="margin-top: 12px"></div>
      <div id="opsError" class="error" style="margin-top: 10px"></div>
    </div>
  `);

  const bodyEl = wrap.querySelector("#opsBody");
  const errEl = wrap.querySelector("#opsError");
  const refreshBtn = wrap.querySelector("#opsRefreshBtn");
  const copyBtn = wrap.querySelector("#opsCopyBtn");

  let lastJson = "";

  function setCopyButtonState(text) {
    copyBtn.textContent = text;
    setTimeout(() => {
      copyBtn.textContent = "复制 JSON";
    }, 1200);
  }

  async function load() {
    errEl.textContent = "";
    bodyEl.innerHTML = `<div class="muted">加载中...</div>`;

    try {
      const data = await api.get("/api/ops/runtime");
      lastJson = JSON.stringify(data, null, 2);

      const status = String(data.status || "");
      const version = data.version ? `v${data.version}` : "";
      const warnings = Array.isArray(data.warnings) ? data.warnings : [];
      const runtime = data.runtime || {};
      const queue = runtime.queue || {};
      const channels = runtime.channels || {};
      const registered = Array.isArray(channels.registered) ? channels.registered : [];
      const channelStatus = channels.status || {};

      const noticeClass = status === "ready" ? "ok" : "warn";
      const warningsHtml = warnings.length
        ? `<ul style="margin: 10px 0 0 18px">${warnings
            .map((w) => `<li>${escapeHtml(w)}</li>`)
            .join("")}</ul>`
        : `<div class="muted" style="margin-top: 8px">暂无告警</div>`;

      bodyEl.innerHTML = `
        <div class="notice ${escapeHtml(noticeClass)}">
          <div style="font-weight: 700">${escapeHtml(statusLabel(status))} ${escapeHtml(version)}</div>
          ${warningsHtml}
        </div>

        <div class="grid" style="margin-top: 12px">
          <div class="panel">
            <h3 style="margin-top: 0">运行时间</h3>
            <div class="muted">启动时间：${escapeHtml(formatTime(runtime.started_at || ""))}</div>
            <div class="muted">运行时长（秒）：${escapeHtml(String(runtime.uptime_seconds ?? ""))}</div>
          </div>

          <div class="panel">
            <h3 style="margin-top: 0">队列</h3>
            <div class="muted">入站：${escapeHtml(String(queue.inbound_depth ?? 0))} / ${escapeHtml(
              String(queue.inbound_capacity ?? 0)
            )} (${escapeHtml(formatPercent(queue.inbound_utilization))})</div>
            <div class="muted">出站：${escapeHtml(String(queue.outbound_depth ?? 0))} / ${escapeHtml(
              String(queue.outbound_capacity ?? 0)
            )} (${escapeHtml(formatPercent(queue.outbound_utilization))})</div>
          </div>

          <div class="panel">
            <h3 style="margin-top: 0">渠道</h3>
            <div class="muted">已注册：${escapeHtml(registered.join(", ") || "（无）")}</div>
            <div class="muted">活跃 Web 连接：${escapeHtml(
              String(channels.active_web_connections ?? 0)
            )}</div>
            <div class="muted" style="margin-top: 8px">状态：</div>
            <pre class="code" style="margin-top: 6px">${escapeHtml(
              JSON.stringify(channelStatus, null, 2)
            )}</pre>
          </div>

          <div class="panel">
            <h3 style="margin-top: 0">原始 JSON</h3>
            <pre class="code" style="margin-top: 6px">${escapeHtml(lastJson)}</pre>
          </div>
        </div>
      `;
    } catch (err) {
      const msg = String((err && err.message) || err || "加载失败");
      if (msg.toLowerCase().includes("insufficient role")) {
        errEl.textContent = "运维页面仅对 Owner 角色开放。";
      } else {
        errEl.textContent = msg;
      }
      lastJson = JSON.stringify({ error: msg }, null, 2);
      bodyEl.innerHTML = "";
    }
  }

  refreshBtn.addEventListener("click", () => {
    load().catch(() => {});
  });

  copyBtn.addEventListener("click", async () => {
    const ok = await copyTextToClipboard(lastJson || "");
    setCopyButtonState(ok ? "已复制" : "复制失败");
    showToast({
      title: ok ? "已复制" : "复制失败",
      message: ok ? "运维快照已复制到剪贴板。" : "请重试或手动复制 JSON。",
      kind: ok ? "success" : "error",
    });
  });

  container.innerHTML = "";
  container.appendChild(wrap);
  await load();

  return () => {};
}
