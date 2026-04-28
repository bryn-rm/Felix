"use client";

import type { AuthError, Session } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";

// Refresh the access token this many seconds before it expires. Supabase
// access tokens default to 3600s; 120s gives the next request comfortable
// headroom even if it queues briefly after a tab resume. Don't tighten this
// without considering the resume race the helper exists to fix.
const EXPIRY_SKEW_SECONDS = 120;

type RefreshOutcome =
  | { kind: "session"; session: Session }
  | { kind: "auth-error" }
  | { kind: "network-error" };

let refreshPromise: Promise<RefreshOutcome> | null = null;

function isExpiringSoon(session: Session | null): boolean {
  if (!session?.access_token) return true;
  if (!session.expires_at) return false;
  return session.expires_at <= Math.floor(Date.now() / 1000) + EXPIRY_SKEW_SECONDS;
}

function isTransient(error: AuthError | null | undefined): boolean {
  if (!error) return true;
  if (error.name === "AuthRetryableFetchError") return true;
  const status = (error as { status?: number }).status;
  return status === undefined || status >= 500;
}

async function refreshSessionOnce(): Promise<RefreshOutcome> {
  if (!refreshPromise) {
    refreshPromise = supabase.auth
      .refreshSession()
      .then(({ data, error }): RefreshOutcome => {
        if (data.session) return { kind: "session", session: data.session };
        return isTransient(error) ? { kind: "network-error" } : { kind: "auth-error" };
      })
      .catch((): RefreshOutcome => ({ kind: "network-error" }))
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export async function getFreshSession(options?: {
  forceRefresh?: boolean;
}): Promise<Session | null> {
  const {
    data: { session: cached },
  } = await supabase.auth.getSession();

  if (!options?.forceRefresh && !isExpiringSoon(cached)) {
    return cached;
  }

  const outcome = await refreshSessionOnce();
  if (outcome.kind === "session") return outcome.session;

  // Network/transient failure: don't log the user out for a Supabase blip.
  // Hand back the cached session so the caller can attempt the request; a
  // truly-revoked auth state will surface through the API's 401 redirect.
  if (outcome.kind === "network-error" && cached?.access_token) {
    return cached;
  }

  return null;
}

export async function getFreshAccessToken(options?: {
  forceRefresh?: boolean;
}): Promise<string | null> {
  const session = await getFreshSession(options);
  return session?.access_token ?? null;
}
