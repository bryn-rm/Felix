import { getFreshAccessToken } from "@/lib/auth-session";
import {
  clearGoogleDisconnected,
  markGoogleDisconnected,
} from "@/lib/google-connection-status";
import { inboxDebug } from "@/lib/inbox-debug";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

// Guard against multiple simultaneous 401 responses each trying to redirect
let _redirecting = false;

/**
 * Navigate to /login on 401, exactly once per page load. 403 is handled
 * via a global "Google disconnected" signal instead, so the user can see
 * and act on the reconnect prompt without being yanked off the current
 * page (especially /settings, where disconnect/reconnect lives).
 */
export function redirectForAuthStatus(status: number): void {
  if (typeof window === "undefined" || _redirecting) return;
  if (status !== 401) return;
  _redirecting = true;
  window.location.href = "/login";
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ---------------------------------------------------------------------------
// Per-call options
// ---------------------------------------------------------------------------

export interface ApiOptions {
  /**
   * Suppress the global 401→/login and 403→/connect navigation for this
   * call. Use when the caller is responsible for its own auth-error
   * recovery (e.g. an SWR hook with a retry policy that wants to retry
   * transient 403s before giving up). Errors are still thrown as normal.
   */
  skipAuthRedirect?: boolean;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function getAuthHeader(): Promise<string> {
  const token = await getFreshAccessToken();
  if (token) {
    return `Bearer ${token}`;
  }

  inboxDebug("api:no-session");
  throw new ApiError(401, "No active session");
}

async function doFetch(
  method: string,
  path: string,
  body: unknown,
  authorization: string,
): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      Authorization: authorization,
      "Content-Type": "application/json",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options?: ApiOptions,
): Promise<T> {
  let authorization = await getAuthHeader();
  let res = await doFetch(method, path, body, authorization);

  // The browser throttles Supabase's silent refresh while the tab is hidden,
  // so the cached access token may be expired on the first call after focus.
  // Try a single refresh + replay before giving up and bouncing to /login.
  if (res.status === 401) {
    const token = await getFreshAccessToken({ forceRefresh: true });
    if (token) {
      authorization = `Bearer ${token}`;
      res = await doFetch(method, path, body, authorization);
    }
  }

  if (!res.ok) {
    let message = res.statusText;
    try {
      const json = await res.json();
      message = json?.detail ?? json?.message ?? message;
    } catch {
      // keep statusText
    }
    inboxDebug("api:status", { path, status: res.status });
    const err = new ApiError(res.status, message);
    if (res.status === 403 && !options?.skipAuthRedirect) {
      markGoogleDisconnected();
    } else if (!options?.skipAuthRedirect) {
      redirectForAuthStatus(res.status);
    }
    throw err;
  }

  clearGoogleDisconnected();

  // 204 No Content
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Public API methods
// ---------------------------------------------------------------------------

export const api = {
  get: <T>(path: string, options?: ApiOptions) =>
    request<T>("GET", path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: ApiOptions) =>
    request<T>("POST", path, body, options),
  put: <T>(path: string, body?: unknown, options?: ApiOptions) =>
    request<T>("PUT", path, body, options),
  patch: <T>(path: string, body?: unknown, options?: ApiOptions) =>
    request<T>("PATCH", path, body, options),
  del: <T>(path: string, options?: ApiOptions) =>
    request<T>("DELETE", path, undefined, options),

  /** Returns a ReadableStream for the streaming draft endpoint. */
  streamDraft: async (emailId: string): Promise<ReadableStream<Uint8Array>> => {
    const path = `/emails/${emailId}/draft`;
    let authorization = await getAuthHeader();
    let res = await doFetch("POST", path, undefined, authorization);

    if (res.status === 401) {
      const token = await getFreshAccessToken({ forceRefresh: true });
      if (token) {
        authorization = `Bearer ${token}`;
        res = await doFetch("POST", path, undefined, authorization);
      }
    }

    if (!res.ok) {
      let message = res.statusText;
      try {
        const json = await res.json();
        message = json?.detail ?? json?.message ?? message;
      } catch {
        // keep statusText
      }
      throw new ApiError(res.status, message);
    }

    if (!res.body) {
      throw new ApiError(500, "No response body for streaming draft");
    }

    return res.body;
  },
};
