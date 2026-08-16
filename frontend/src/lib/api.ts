/**
 * Thin API client.
 *
 * Two rules the UI depends on:
 *  - a failed request never throws into a page; it resolves to `{ error }` so
 *    the panel can render "temporarily unavailable" instead of a blank screen;
 *  - the auth token lives in memory + localStorage and is attached here, never
 *    passed around components.
 */

const TOKEN_KEY = "mii.token";

export type ApiResult<T> = { data: T; error: null } | { data: null; error: string };

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

function base(): string {
  // On the server we call the backend directly; in the browser we go through
  // the Next rewrite so the API origin is never in the client bundle.
  if (typeof window === "undefined") {
    return process.env.BACKEND_URL || "http://127.0.0.1:8000";
  }
  return "";
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit & { auth?: boolean } = {},
): Promise<ApiResult<T>> {
  const { auth = true, headers, ...rest } = init;
  const token = auth ? getToken() : null;

  try {
    const response = await fetch(`${base()}${path}`, {
      ...rest,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(headers || {}),
      },
      cache: "no-store",
    });

    if (response.status === 204) return { data: null as T, error: null };

    const text = await response.text();
    const payload = text ? safeParse(text) : null;

    if (!response.ok) {
      const detail =
        (payload && (payload.detail || payload.error || payload.message)) ||
        `Request failed (HTTP ${response.status})`;
      return {
        data: null,
        error: typeof detail === "string" ? detail : JSON.stringify(detail),
      };
    }
    return { data: payload as T, error: null };
  } catch (err) {
    return {
      data: null,
      error:
        err instanceof Error
          ? `Could not reach the API: ${err.message}`
          : "Could not reach the API.",
    };
  }
}

function safeParse(text: string) {
  try {
    return JSON.parse(text);
  } catch {
    return { error: text.slice(0, 400) };
  }
}

export const api = {
  get: <T,>(path: string, auth = true) => apiFetch<T>(path, { method: "GET", auth }),
  post: <T,>(path: string, body?: unknown, auth = true) =>
    apiFetch<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
      auth,
    }),
  patch: <T,>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T,>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: <T,>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
};
