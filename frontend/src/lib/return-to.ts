/**
 * Where to send someone after they sign in.
 *
 * Only ever an in-app path. The value travels through places a stranger can
 * influence — a `?next=` query string, a cookie, a request header — so it is
 * validated on every read rather than trusted from whoever wrote it, and a
 * value that isn't obviously a same-origin path is dropped rather than
 * repaired. That is what keeps `/login?next=https://evil.example` from turning
 * Felix's sign-in into an open redirect.
 *
 * No auth decision depends on any of this: it names a destination, never a
 * permission. The route the user lands on runs its own ownership checks.
 */

/**
 * Request header the middleware stamps with the path being rendered, so a
 * Server Component (which cannot otherwise know it) can send an unauthenticated
 * visitor back to the page they actually asked for.
 */
export const PATHNAME_HEADER = "x-felix-pathname";

/** Cookie carrying the return path across the Google OAuth round trip. */
export const RETURN_TO_COOKIE = "felix_return_to";

/** Long enough for a sign-in, short enough that a stale one can't linger. */
export const RETURN_TO_MAX_AGE_S = 600;

/** Keep every hop aligned with the backend validator and bound OAuth state. */
export const RETURN_TO_MAX_LENGTH = 2048;

// Any origin that cannot be a real one, so an input that resolves somewhere
// else shows up as an origin mismatch rather than as a missed pattern.
const PROBE_ORIGIN = "https://felix.invalid";

// Control characters: header/cookie smuggling, and never legitimate in a path.
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

/**
 * Validate a return destination, or null if it isn't a safe in-app path.
 *
 * Rejects absolute URLs, protocol-relative ones (`//evil.example`), the
 * backslash variants browsers normalise into them, and anything carrying
 * control characters. The fragment is dropped — nothing needs it, and it is one
 * less thing to carry through a redirect.
 */
export function safeReturnPath(raw: string | null | undefined): string | null {
  if (!raw || typeof raw !== "string") return null;
  if (raw.length > RETURN_TO_MAX_LENGTH) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//") || raw.startsWith("/\\")) return null;
  if (CONTROL_CHARS.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw, PROBE_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== PROBE_ORIGIN) return null;
  return `${url.pathname}${url.search}`;
}

/** `/login`, with a validated return path attached when there is one. */
export function loginUrlFor(raw: string | null | undefined): string {
  const next = safeReturnPath(raw);
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

/** `/connect`, with a validated return path attached when there is one. */
export function connectUrlFor(raw: string | null | undefined): string {
  const next = safeReturnPath(raw);
  return next ? `/connect?next=${encodeURIComponent(next)}` : "/connect";
}

/**
 * Hand the return path to the OAuth round trip (browser only).
 *
 * A cookie rather than a `redirectTo` query parameter: the redirect URL Supabase
 * is given has to keep matching the project's allow-list exactly, and a
 * destination the user picked has no business being inside it. SameSite=Lax is
 * enough — the callback arrives as a top-level GET navigation.
 */
export function rememberReturnPath(raw: string | null | undefined): void {
  if (typeof document === "undefined") return;
  const next = safeReturnPath(raw);
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  if (!next) {
    // A fresh sign-in without a valid destination supersedes any abandoned
    // OAuth attempt. Otherwise its still-live cookie can steer this unrelated
    // sign-in after the current URL deliberately supplied no usable `next`.
    document.cookie =
      `${RETURN_TO_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
    return;
  }
  document.cookie =
    `${RETURN_TO_COOKIE}=${encodeURIComponent(next)}; Path=/; ` +
    `Max-Age=${RETURN_TO_MAX_AGE_S}; SameSite=Lax${secure}`;
}
