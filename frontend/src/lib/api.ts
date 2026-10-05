/**
 * Thin client for the FastAPI backend.
 *
 * The bearer token lives in sessionStorage only (gone when the tab closes); nothing is cached
 * client-side, so every page shows what the backend returns at that moment. A 401 clears the
 * token and the calling page sends the user back to /login.
 */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8080").replace(/\/+$/, "");

const TOKEN_KEY = "govcareeros.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked: the session simply will not survive a reload
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detail(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  // FastAPI validation errors: [{loc, msg}, ...]
  if (Array.isArray(d)) return d.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
  return fallback;
}

export async function api<T>(
  path: string,
  init: { method?: string; body?: unknown; form?: FormData; auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  // A FormData body (file uploads) sets its own multipart Content-Type with the boundary
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (init.auth !== false && token) headers["Authorization"] = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: init.method || (init.form ? "POST" : "GET"),
      headers,
      body: init.form ?? (init.body === undefined ? undefined : JSON.stringify(init.body)),
    });
  } catch {
    throw new ApiError(0, `cannot reach the API at ${API_URL}`);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, detail(body, res.statusText));
  }
  return body as T;
}
