import { supabase } from "@/lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

// Guard against multiple simultaneous 401/403 responses each trying to redirect
let _redirecting = false;

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
// Internal helpers
// ---------------------------------------------------------------------------

async function getAuthHeader(): Promise<string> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (session?.access_token) {
    return `Bearer ${session.access_token}`;
  }

  // Force a round-trip so Supabase rehydrates the session from storage.
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (user) {
    const {
      data: { session: refreshed },
    } = await supabase.auth.getSession();
    if (refreshed?.access_token) {
      return `Bearer ${refreshed.access_token}`;
    }
  }

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
): Promise<T> {
  let authorization = await getAuthHeader();
  let res = await doFetch(method, path, body, authorization);

  // The browser throttles Supabase's silent refresh while the tab is hidden,
  // so the cached access token may be expired on the first call after focus.
  // Try a single refresh + replay before giving up and bouncing to /login.
  if (res.status === 401) {
    try {
      const { data } = await supabase.auth.refreshSession();
      if (data.session?.access_token) {
        authorization = `Bearer ${data.session.access_token}`;
        res = await doFetch(method, path, body, authorization);
      }
    } catch {
      // fall through to the redirect handling below
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
    const err = new ApiError(res.status, message);
    if (typeof window !== "undefined" && !_redirecting) {
      if (res.status === 401) {
        _redirecting = true;
        window.location.href = "/login";
      } else if (res.status === 403) {
        _redirecting = true;
        window.location.href = "/connect";
      }
    }
    throw err;
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Public API methods
// ---------------------------------------------------------------------------

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),

  /** Returns a ReadableStream for the streaming draft endpoint. */
  streamDraft: async (emailId: string): Promise<ReadableStream<Uint8Array>> => {
    const authorization = await getAuthHeader();

    const res = await fetch(`${API_BASE}/emails/${emailId}/draft`, {
      method: "POST",
      headers: {
        Authorization: authorization,
        "Content-Type": "application/json",
      },
    });

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
