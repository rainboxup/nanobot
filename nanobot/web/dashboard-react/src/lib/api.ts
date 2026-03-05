import { useStore } from "@/src/store/useStore";

const TOKEN_KEY = "nanobot_jwt";
const REFRESH_TOKEN_KEY = "nanobot_refresh_token";

export function getAccessToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function getRefreshToken(): string {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setAuthTokens(accessToken: string, refreshToken?: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, accessToken || "");
    // Keep refresh token out of localStorage; cookie mode is preferred.
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    // Accept the second parameter for backward-compatible call sites.
    void refreshToken;
  } catch {
    // ignore storage failures
  }
}

export function clearAuthTokens(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    // ignore storage failures
  }
}

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail || `HTTP ${status}`;
  }
}

export function handleUnauthorized(detail: string = "Unauthorized"): ApiError {
  clearAuthTokens();
  try {
    useStore.getState().setUser(null);
  } catch {
    // ignore store access failures
  }
  if (window.location.hash !== "#/login") {
    window.location.hash = "#/login";
  }
  return new ApiError(401, detail);
}

let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  // Cookie is the source of truth for refresh; ignore legacy localStorage refresh tokens.
  const res = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({}),
  });

  if (!res.ok) return false;
  const data = (await res.json().catch(() => null)) as any;
  if (!data || !data.access_token) return false;
  setAuthTokens(String(data.access_token || ""), String(data.refresh_token || ""));
  return true;
}

async function ensureFreshToken(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = refreshAccessToken().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

export async function tryRefreshAccessToken(): Promise<boolean> {
  return ensureFreshToken();
}

function _normalizeHeaders(initHeaders?: HeadersInit): Record<string, string> {
  const headers: Record<string, string> = {};
  if (!initHeaders) return headers;
  try {
    const h = new Headers(initHeaders);
    h.forEach((value, key) => {
      headers[String(key)] = String(value);
    });
    return headers;
  } catch {
    return headers;
  }
}

function _bodyInit(value: unknown, headers: Record<string, string>): BodyInit | undefined {
  if (value === undefined) return undefined;
  if (value === null) return undefined;
  if (typeof value === "string") return value;
  if (typeof FormData !== "undefined" && value instanceof FormData) return value;
  if (typeof URLSearchParams !== "undefined" && value instanceof URLSearchParams) return value;
  if (typeof Blob !== "undefined" && value instanceof Blob) return value;
  if (typeof ArrayBuffer !== "undefined" && value instanceof ArrayBuffer) return value;
  if (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView(value as any)) return value as any;

  if (!headers["Content-Type"]) headers["Content-Type"] = "application/json";
  return JSON.stringify(value);
}

async function _readResponseData(res: Response): Promise<any> {
  const text = await res.text().catch(() => "");
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function _formatApiErrorDetail(data: any, fallback: string): string {
  if (data == null) return fallback;
  const detail = typeof data === "object" ? (data.detail ?? data.error) : data;

  if (Array.isArray(detail)) {
    const lines = detail
      .map((item) => {
        if (!item || typeof item !== "object") return String(item || "").trim();
        const loc = Array.isArray((item as any).loc)
          ? (item as any).loc.map((x: any) => String(x)).join(".")
          : "";
        const msg = String((item as any).msg || "").trim();
        if (loc && msg) return `${loc}: ${msg}`;
        return msg || loc;
      })
      .filter(Boolean);
    return lines.join("; ") || fallback;
  }

  if (typeof detail === "string") {
    return detail.trim() || fallback;
  }

  if (detail && typeof detail === "object") {
    const msg = String((detail as any).msg || (detail as any).message || "").trim();
    if (msg) return msg;
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }

  const text = String(detail || "").trim();
  return text || fallback;
}

async function requestResponse(
  method: string,
  path: string,
  init?: RequestInit,
  retryOnAuthFailure: boolean = true
): Promise<Response> {
  const headers: Record<string, string> = {
    ...authHeaders(),
    ..._normalizeHeaders(init?.headers),
  };

  const res = await fetch(path, {
    ...init,
    method,
    credentials: init?.credentials ?? "include",
    headers,
    body: _bodyInit((init as any)?.body, headers),
  });

  if (res.status === 401 && retryOnAuthFailure && path !== "/api/auth/refresh") {
    const refreshed = await ensureFreshToken();
    if (refreshed) {
      return requestResponse(method, path, init, false);
    }
  }

  if (res.status === 401) {
    throw handleUnauthorized("Unauthorized");
  }

  if (!res.ok) {
    const data = await _readResponseData(res.clone());
    const detail = _formatApiErrorDetail(data, String(res.statusText || `HTTP ${res.status}`));
    throw new ApiError(res.status, detail);
  }

  return res;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  retryOnAuthFailure: boolean = true
): Promise<T> {
  const res = await requestResponse(method, path, { body: body as any }, retryOnAuthFailure);
  const data = await _readResponseData(res);
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
  fetch: (path: string, init?: RequestInit) =>
    requestResponse(String(init?.method || "GET"), path, init),
};

export function wsUrl(path: string, params?: Record<string, string>): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = new URL(`${proto}://${window.location.host}${path}`);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v);
    }
  }
  return url.toString();
}

export function formatTime(isoOrMs: string | number): string {
  try {
    const d =
      typeof isoOrMs === "number"
        ? new Date(isoOrMs)
        : new Date(isoOrMs || Date.now());
    return d.toLocaleString();
  } catch {
    return "";
  }
}
