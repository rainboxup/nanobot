const TOKEN_KEY = "nanobot_jwt";
const REFRESH_TOKEN_KEY = "nanobot_refresh_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function setRefreshToken(token) {
  if (token) localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_TOKEN_KEY) || "";
}

export function setAuthTokens(accessToken, refreshToken) {
  setToken(accessToken || "");
  if (refreshToken) setRefreshToken(refreshToken);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function refreshAccessToken() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  const res = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) return false;

  const data = await res.json();
  if (!data || !data.access_token) return false;
  setAuthTokens(data.access_token, data.refresh_token || "");
  return true;
}

async function request(method, path, body, retryOnAuthFailure = true) {
  const headers = {
    "Content-Type": "application/json",
    ...authHeaders(),
  };

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 401 && retryOnAuthFailure && path !== "/api/auth/refresh") {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request(method, path, body, false);
    }
  }

  if (res.status === 401) {
    clearToken();
    window.location.hash = "#/login";
    throw new Error("Unauthorized");
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const msg = typeof data === "object" && data && data.detail ? data.detail : res.statusText;
    throw new Error(msg || `HTTP ${res.status}`);
  }

  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  delete: (path) => request("DELETE", path),
};

export function wsUrl(path, params) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = new URL(`${proto}://${window.location.host}${path}`);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v);
    }
  }
  return url.toString();
}

export function formatTime(isoOrMs) {
  try {
    const d = typeof isoOrMs === "number" ? new Date(isoOrMs) : new Date(isoOrMs || Date.now());
    return d.toLocaleString();
  } catch {
    return "";
  }
}

