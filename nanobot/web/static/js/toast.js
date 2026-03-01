let toastRoot = null;
let toastSeq = 0;

const DEFAULT_DURATIONS = {
  info: 2600,
  success: 2200,
  warn: 3600,
  error: 6000,
};

const toastConfig = {
  maxCount: 4,
  pauseOnHover: true,
  pauseOnFocus: true,
  dismissOnClick: false,
  durationByKind: { ...DEFAULT_DURATIONS },
};

const activeToasts = new Map();
const SVG_NS = "http://www.w3.org/2000/svg";

function isCurrent(rec) {
  return Boolean(rec && activeToasts.get(rec.key) === rec);
}

function dismissToastRecord(rec) {
  if (!isCurrent(rec)) return;
  dismissToast(rec.key);
}

function normalizeKind(kind) {
  const k = String(kind || "").toLowerCase();
  if (k === "info" || k === "success" || k === "warn" || k === "error") return k;
  return "info";
}

function normalizeKey(value) {
  const raw = String(value ?? "").trim();
  return raw ? raw : null;
}

function normalizeDurationMs(durationMs, kind) {
  if (durationMs === undefined || durationMs === null || durationMs === "") {
    return Number(toastConfig.durationByKind[kind] ?? DEFAULT_DURATIONS[kind] ?? 2600);
  }

  const n = Number(durationMs);
  if (!Number.isFinite(n)) return Number(toastConfig.durationByKind[kind] ?? 2600);
  if (n <= 0) return 0;
  return Math.max(800, Math.round(n));
}

function getDefaultRegionLabel() {
  const lang = String(document.documentElement.getAttribute("lang") || navigator.language || "").toLowerCase();
  if (lang.startsWith("zh")) return "通知";
  return "Notifications";
}

function getDefaultCloseLabel() {
  const lang = String(document.documentElement.getAttribute("lang") || navigator.language || "").toLowerCase();
  if (lang.startsWith("zh")) return "关闭通知";
  return "Dismiss notification";
}

function ensureToastRootAttributes(root) {
  if (!root) return;
  if (!root.id) root.id = "toastRoot";
  root.classList.add("toast-root");
  if (!root.hasAttribute("role")) root.setAttribute("role", "region");
  if (!root.hasAttribute("aria-label")) root.setAttribute("aria-label", getDefaultRegionLabel());

  // Avoid nested live regions: each toast carries its own role/aria-live.
  root.removeAttribute("aria-live");
  root.removeAttribute("aria-relevant");
  root.removeAttribute("aria-atomic");
}

function resolveRoot() {
  if (toastRoot) return toastRoot;
  toastRoot = document.getElementById("toastRoot");
  if (toastRoot) {
    ensureToastRootAttributes(toastRoot);
    return toastRoot;
  }

  toastRoot = document.createElement("div");
  toastRoot.id = "toastRoot";
  ensureToastRootAttributes(toastRoot);
  document.body.appendChild(toastRoot);
  return toastRoot;
}

function parseTimeMs(value) {
  const v = String(value || "").trim();
  if (!v) return 0;
  if (v.endsWith("ms")) return Number.parseFloat(v) || 0;
  if (v.endsWith("s")) return (Number.parseFloat(v) || 0) * 1000;
  const n = Number.parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function getTransitionMs(el) {
  try {
    const s = getComputedStyle(el);
    const durations = String(s.transitionDuration || "0s")
      .split(",")
      .map((x) => parseTimeMs(x));
    const delays = String(s.transitionDelay || "0s")
      .split(",")
      .map((x) => parseTimeMs(x));

    const len = Math.max(durations.length, delays.length);
    let max = 0;
    for (let i = 0; i < len; i++) {
      const d = durations[i % durations.length] || 0;
      const l = delays[i % delays.length] || 0;
      max = Math.max(max, d + l);
    }
    return Math.max(0, Math.round(max));
  } catch {
    return 0;
  }
}

function createIconPath(kind) {
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");

  if (kind === "success") {
    path.setAttribute(
      "d",
      "M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    );
    return [path];
  }
  if (kind === "warn") {
    path.setAttribute(
      "d",
      "M12 9v3.75m9.303 3.376c.866 1.5-.217 3.374-1.948 3.374H4.645c-1.731 0-2.814-1.874-1.948-3.374L10.05 3.378c.866-1.5 3.032-1.5 3.898 0l7.355 12.748zM12 15.75h.008v.008H12v-.008z"
    );
    return [path];
  }
  if (kind === "error") {
    path.setAttribute(
      "d",
      "M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    );
    return [path];
  }

  // info
  path.setAttribute(
    "d",
    "M11.25 11.25h1.5v4.5h1.5m-3-7.5h.008v.008h-.008V8.25M12 21a9 9 0 110-18 9 9 0 010 18z"
  );
  return [path];
}

function setToastA11y(el, kind) {
  const isError = kind === "error";
  el.setAttribute("role", isError ? "alert" : "status");
  el.setAttribute("aria-live", isError ? "assertive" : "polite");
  el.setAttribute("aria-atomic", "true");
}

function setToastKind(el, liveEl, kind) {
  el.classList.remove("info", "success", "warn", "error");
  el.classList.add(kind);
  // Keep the live region limited to text content (avoid announcing button labels).
  el.removeAttribute("role");
  el.removeAttribute("aria-live");
  el.removeAttribute("aria-relevant");
  el.removeAttribute("aria-atomic");
  if (liveEl) setToastA11y(liveEl, kind);
}

function createToastElements({ title, message, kind, closeLabel, actionLabel }) {
  const el = document.createElement("div");
  el.className = "toast";

  const row = document.createElement("div");
  row.className = "toast-row";
  el.appendChild(row);

  const iconWrap = document.createElement("div");
  iconWrap.className = "toast-icon-wrap";
  iconWrap.setAttribute("aria-hidden", "true");
  row.appendChild(iconWrap);

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.classList.add("toast-icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  for (const p of createIconPath(kind)) svg.appendChild(p);
  iconWrap.appendChild(svg);

  const body = document.createElement("div");
  body.className = "toast-body";
  row.appendChild(body);

  const liveEl = document.createElement("div");
  liveEl.className = "toast-live";
  body.appendChild(liveEl);

  const titleEl = document.createElement("div");
  titleEl.className = "toast-title";
  titleEl.textContent = String(title || "");
  titleEl.hidden = !titleEl.textContent;
  liveEl.appendChild(titleEl);

  const msgEl = document.createElement("div");
  msgEl.className = "toast-msg";
  msgEl.textContent = String(message || "");
  msgEl.hidden = !msgEl.textContent;
  liveEl.appendChild(msgEl);

  const actionsEl = document.createElement("div");
  actionsEl.className = "toast-actions";
  actionsEl.hidden = true;
  body.appendChild(actionsEl);

  let actionBtn = null;
  if (actionLabel) {
    actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.className = "toast-action";
    actionBtn.textContent = String(actionLabel);
    actionsEl.hidden = false;
    actionsEl.appendChild(actionBtn);
  }

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "toast-close";
  closeBtn.setAttribute("aria-label", String(closeLabel || getDefaultCloseLabel()));
  closeBtn.setAttribute("title", String(closeLabel || getDefaultCloseLabel()));
  closeBtn.textContent = "×";
  row.appendChild(closeBtn);

  setToastKind(el, liveEl, kind);
  return { el, svg, liveEl, titleEl, msgEl, actionsEl, actionBtn, closeBtn };
}

function startTimer(rec) {
  if (!rec || rec.durationMs <= 0) return;
  if (!isCurrent(rec)) return;
  if (rec.timerId) clearTimeout(rec.timerId);
  rec.startedAt = Date.now();
  rec.timerId = setTimeout(() => dismissToastRecord(rec), Math.max(1, rec.remainingMs));
}

function pauseTimer(rec) {
  if (!rec || rec.durationMs <= 0) return;
  if (!isCurrent(rec)) return;
  if (!rec.timerId) return;
  const elapsed = Date.now() - (rec.startedAt || Date.now());
  rec.remainingMs = Math.max(0, rec.remainingMs - elapsed);
  clearTimeout(rec.timerId);
  rec.timerId = null;
  rec.startedAt = 0;
}

function resumeTimer(rec) {
  if (!rec || rec.durationMs <= 0) return;
  if (!isCurrent(rec)) return;
  if (rec.timerId) return;
  if (rec.remainingMs <= 0) {
    dismissToastRecord(rec);
    return;
  }
  startTimer(rec);
}

function syncPause(rec) {
  if (!rec || rec.durationMs <= 0) return;
  if (!isCurrent(rec)) return;
  const shouldPause = (toastConfig.pauseOnHover && rec.hovered) || (toastConfig.pauseOnFocus && rec.focused);
  if (shouldPause) pauseTimer(rec);
  else resumeTimer(rec);
}

function enforceMaxCount() {
  const max = Number(toastConfig.maxCount);
  if (!Number.isFinite(max) || max <= 0) return;
  while (activeToasts.size > max) {
    const oldestKey = activeToasts.keys().next().value;
    if (!oldestKey) break;
    dismissToast(oldestKey);
  }
}

export function configureToast(options = {}) {
  if (!options || typeof options !== "object") return;

  if (options.maxCount !== undefined) {
    const n = Number(options.maxCount);
    if (Number.isFinite(n) && n >= 0) toastConfig.maxCount = Math.floor(n);
  }
  if (options.pauseOnHover !== undefined) toastConfig.pauseOnHover = Boolean(options.pauseOnHover);
  if (options.pauseOnFocus !== undefined) toastConfig.pauseOnFocus = Boolean(options.pauseOnFocus);
  if (options.dismissOnClick !== undefined) toastConfig.dismissOnClick = Boolean(options.dismissOnClick);

  if (options.durationByKind && typeof options.durationByKind === "object") {
    for (const k of Object.keys(DEFAULT_DURATIONS)) {
      if (options.durationByKind[k] === undefined) continue;
      const n = Number(options.durationByKind[k]);
      if (!Number.isFinite(n)) continue;
      toastConfig.durationByKind[k] = n <= 0 ? 0 : Math.max(800, Math.round(n));
    }
  }

  enforceMaxCount();
}

export function initToast(container, options) {
  toastRoot = container || null;
  const root = resolveRoot();
  ensureToastRootAttributes(root);
  if (options) configureToast(options);
}

export function clearToasts() {
  for (const key of Array.from(activeToasts.keys())) dismissToast(key);
}

export function dismissToast(key) {
  const k = String(key || "");
  const rec = activeToasts.get(k);
  if (!rec) return;

  activeToasts.delete(k);

  if (rec.timerId) clearTimeout(rec.timerId);
  if (rec.cleanupTimerId) clearTimeout(rec.cleanupTimerId);

  const el = rec.el;
  el.classList.remove("show");
  el.classList.add("hide");

  const cleanup = () => {
    try {
      el.removeEventListener("transitionend", cleanup);
      el.remove();
    } catch {
      // ignore
    }
  };

  el.addEventListener("transitionend", cleanup);
  const ms = getTransitionMs(el);
  rec.cleanupTimerId = setTimeout(cleanup, Math.min(15000, Math.max(200, ms + 80)));
}

export function showToast({
  id,
  key,
  title = "",
  message = "",
  kind = "info",
  durationMs,
  closeLabel,
  action,
} = {}) {
  const root = resolveRoot();

  const safeKind = normalizeKind(kind);
  const toastKey = normalizeKey(id) || normalizeKey(key) || `t${++toastSeq}`;

  const actionLabel =
    action && typeof action === "object" && action.label ? String(action.label) : "";
  const onAction =
    action && typeof action === "object" && typeof action.onClick === "function" ? action.onClick : null;
  const dismissOnAction =
    action && typeof action === "object" && action.dismissOnAction !== undefined
      ? Boolean(action.dismissOnAction)
      : true;

  const dur = normalizeDurationMs(durationMs, safeKind);

  const existing = activeToasts.get(toastKey);
  if (existing) {
    existing.titleEl.textContent = String(title || "");
    existing.titleEl.hidden = !existing.titleEl.textContent;
    existing.msgEl.textContent = String(message || "");
    existing.msgEl.hidden = !existing.msgEl.textContent;
    setToastKind(existing.el, existing.liveEl, safeKind);

    // Replace icon paths when kind changes.
    while (existing.svg.firstChild) existing.svg.removeChild(existing.svg.firstChild);
    for (const p of createIconPath(safeKind)) existing.svg.appendChild(p);

    if (existing.actionBtn) existing.actionBtn.remove();
    existing.actionBtn = null;
    existing.actionsEl.hidden = true;
    if (actionLabel) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "toast-action";
      btn.textContent = actionLabel;
      existing.actionsEl.hidden = false;
      existing.actionsEl.appendChild(btn);
      existing.actionBtn = btn;
      if (onAction) {
        btn.addEventListener("click", () => {
          try {
            onAction();
          } finally {
            if (dismissOnAction) dismissToastRecord(existing);
          }
        });
      }
    }

    existing.closeBtn.setAttribute("aria-label", String(closeLabel || getDefaultCloseLabel()));
    existing.closeBtn.setAttribute("title", String(closeLabel || getDefaultCloseLabel()));

    existing.durationMs = dur;
    existing.remainingMs = dur;
    if (existing.timerId) clearTimeout(existing.timerId);
    existing.timerId = null;
    existing.startedAt = 0;
    if (dur > 0) {
      const paused =
        (toastConfig.pauseOnHover && existing.hovered) || (toastConfig.pauseOnFocus && existing.focused);
      if (!paused) startTimer(existing);
    }

    // Treat updated toast as newest.
    activeToasts.delete(toastKey);
    activeToasts.set(toastKey, existing);
    root.appendChild(existing.el);
    enforceMaxCount();
    return toastKey;
  }

  const { el, svg, liveEl, titleEl, msgEl, actionsEl, actionBtn, closeBtn } = createToastElements({
    title,
    message,
    kind: safeKind,
    closeLabel,
    actionLabel,
  });

  const rec = {
    key: toastKey,
    el,
    svg,
    liveEl,
    titleEl,
    msgEl,
    actionsEl,
    actionBtn,
    closeBtn,
    durationMs: dur,
    remainingMs: dur,
    startedAt: 0,
    timerId: null,
    cleanupTimerId: null,
    hovered: false,
    focused: false,
  };

  closeBtn.addEventListener("click", () => dismissToastRecord(rec));

  if (toastConfig.dismissOnClick) {
    el.addEventListener("click", (e) => {
      if (e.target && (e.target.closest(".toast-close") || e.target.closest(".toast-action"))) return;
      dismissToastRecord(rec);
    });
  }

  if (toastConfig.pauseOnHover) {
    el.addEventListener("mouseenter", () => {
      rec.hovered = true;
      syncPause(rec);
    });
    el.addEventListener("mouseleave", () => {
      rec.hovered = false;
      syncPause(rec);
    });
  }
  if (toastConfig.pauseOnFocus) {
    el.addEventListener("focusin", () => {
      rec.focused = true;
      syncPause(rec);
    });
    el.addEventListener("focusout", (e) => {
      if (e.relatedTarget && el.contains(e.relatedTarget)) return;
      rec.focused = false;
      syncPause(rec);
    });
  }

  if (actionBtn) {
    if (onAction) {
      actionBtn.addEventListener("click", () => {
        try {
          onAction();
        } finally {
          if (dismissOnAction) dismissToastRecord(rec);
        }
      });
    }
  }

  root.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));

  activeToasts.set(toastKey, rec);
  if (dur > 0) startTimer(rec);
  enforceMaxCount();
  return toastKey;
}
