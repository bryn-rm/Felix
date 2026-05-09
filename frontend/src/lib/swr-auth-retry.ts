"use client";

import type { Revalidator, RevalidatorOptions } from "swr";
import { ApiError, redirectForAuthStatus } from "@/lib/api";
import { inboxDebug } from "@/lib/inbox-debug";

const AUTH_MAX_RETRIES = 3;

// SWR's own defaults for non-auth retries. Mirrored here because providing a
// custom onErrorRetry replaces SWR's default handler entirely — we have to
// re-implement the default policy for any error class we don't specialise on.
const DEFAULT_ERROR_RETRY_COUNT = 5;
const DEFAULT_ERROR_RETRY_INTERVAL = 5000;

interface RetryConfig {
  errorRetryCount?: number;
  errorRetryInterval?: number;
}

/**
 * Shared `onErrorRetry` for SWR hooks that hit auth-gated endpoints.
 *
 * Behaviour:
 *  - 401 / 403: retry up to AUTH_MAX_RETRIES with 0.5 / 1 / 2 s backoff.
 *    On exhaustion, navigate to /login (401) or /connect (403). This
 *    closes the gap left by callers that pass `{ skipAuthRedirect: true }`
 *    to the api client to opt out of its eager redirect.
 *  - Other errors (network, 5xx, etc.): mirror SWR's default exponential
 *    backoff using the hook's configured `errorRetryCount` /
 *    `errorRetryInterval`, with the same jitter formula SWR uses
 *    internally. Keeping this behaviour here is essential — SWR replaces
 *    the default once any custom onErrorRetry is provided.
 */
export function authAwareRetry(
  err: unknown,
  key: string,
  config: RetryConfig,
  revalidate: Revalidator,
  { retryCount }: Required<RevalidatorOptions>,
): void {
  const status = err instanceof ApiError ? err.status : undefined;
  inboxDebug("swr:error", { key, status, retryCount });

  if (status === 401 || status === 403) {
    if (retryCount > AUTH_MAX_RETRIES) {
      redirectForAuthStatus(status);
      return;
    }
    const delay = 500 * 2 ** (retryCount - 1);
    setTimeout(() => revalidate({ retryCount }), delay);
    return;
  }

  // Non-auth: replicate SWR's default jittered exponential backoff so we
  // don't accidentally disable retries on transient 5xx / network errors.
  const maxRetryCount = config.errorRetryCount ?? DEFAULT_ERROR_RETRY_COUNT;
  if (retryCount > maxRetryCount) return;

  const interval = config.errorRetryInterval ?? DEFAULT_ERROR_RETRY_INTERVAL;
  const exp = retryCount < 8 ? retryCount : 8;
  const timeout = ~~((Math.random() + 0.5) * (1 << exp)) * interval;
  setTimeout(() => revalidate({ retryCount }), timeout);
}
