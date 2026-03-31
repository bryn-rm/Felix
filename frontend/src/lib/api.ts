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
  // Primary: read session from the Supabase client (localStorage-backed after supabase.ts change)
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (session?.access_token) {
    console.log("[api] getAuthHeader: token from getSession(), user =", session.user?.id);
    return `Bearer ${session.access_token}`;
  }
  console.log("[api] getAuthHeader: getSession() returned no token, trying getUser()");

  // Fallback 1: force a round-trip to Supabase to rehydrate the session
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (user) {
    // getUser() succeeded — session should now be available
    const { data: { session: refreshed } } = await supabase.auth.getSession();
    if (refreshed?.access_token) {
      console.log("[api] getAuthHeader: token from getUser() rehydration, user =", user.id);
      return `Bearer ${refreshed.access_token}`;
    }
  }
  console.log("[api] getAuthHeader: getUser() also returned no session, trying localStorage");

  // Fallback 2: read directly from localStorage using the Supabase key convention
  if (typeof window !== "undefined") {
    const storageKey = `sb-${process.env.NEXT_PUBLIC_SUPABASE_URL?.split("//")[1]?.split(".")[0]}-auth-token`;
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        if (parsed?.access_token) {
          console.log("[api] getAuthHeader: token from localStorage key =", storageKey);
          return `Bearer ${parsed.access_token}`;
        }
      } catch {
        console.log("[api] getAuthHeader: localStorage parse failed for key =", storageKey);
      }
    } else {
      console.log("[api] getAuthHeader: nothing in localStorage at key =", storageKey);
    }
  }

  console.log("[api] getAuthHeader: all fallbacks exhausted — no token found");
  throw new ApiError(401, "No active session");
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const authorization = await getAuthHeader();

  const headers: Record<string, string> = {
    Authorization: authorization,
    "Content-Type": "application/json",
  };

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

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
