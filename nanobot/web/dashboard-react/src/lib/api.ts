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
    if (refreshToken) {
      localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    }
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

let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  const res = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
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
    clearAuthTokens();
    try {
      useStore.getState().setUser(null);
    } catch {
      // ignore store access failures
    }
    // HashRouter: force redirect to login.
    window.location.hash = "#/login";
    throw new ApiError(401, "Unauthorized");
  }

  if (!res.ok) {
    const data = await _readResponseData(res.clone());
    const detail =
      typeof data === "object" && data && (data.detail || data.error)
        ? String(data.detail || data.error)
        : String(res.statusText || `HTTP ${res.status}`);
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
