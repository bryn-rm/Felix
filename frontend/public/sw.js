/**
 * Felix Service Worker
 *
 * Strategy:
 *   - Immutable Next.js static assets: stale-while-revalidate
 *   - API/authenticated/user-data requests: network-only, never cached
 *   - Navigation requests: network-only
 */

const CACHE_NAME = "felix-v2";
const USER_DATA_PATH_PREFIXES = [
  "/emails",
  "/calendar",
  "/briefing",
  "/contacts",
  "/follow-ups",
  "/commitments",
  "/templates",
  "/memory",
  "/meetings",
  "/eval",
  "/admin",
  "/auth",
  "/voice",
  "/polish",
  "/settings",
];

let API_ORIGIN = "";
try {
  const apiBase = new URL(self.location.href).searchParams.get("apiBase");
  API_ORIGIN = apiBase ? new URL(apiBase).origin : "";
} catch {
  API_ORIGIN = "";
}

// ---------------------------------------------------------------------------
// Install — activate this worker immediately
// ---------------------------------------------------------------------------
self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

// ---------------------------------------------------------------------------
// Activate — clean up old caches
// ---------------------------------------------------------------------------
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// ---------------------------------------------------------------------------
// Fetch — routing strategy
// ---------------------------------------------------------------------------
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never intercept API calls, authenticated requests, user-data routes,
  // WebSocket upgrades, navigations, or non-GET requests.
  if (
    request.method !== "GET" ||
    request.mode === "navigate" ||
    request.headers.has("Authorization") ||
    (API_ORIGIN !== "" && url.origin === API_ORIGIN) ||
    url.pathname.startsWith("/api/") ||
    USER_DATA_PATH_PREFIXES.some((prefix) => url.pathname.startsWith(prefix)) ||
    url.protocol === "ws:" ||
    url.protocol === "wss:"
  ) {
    return;
  }

  // Only immutable Next.js build assets are cacheable. Everything else falls
  // through to the browser so private backend responses cannot enter Cache Storage.
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/_next/static/")) {
    return;
  }

  // Immutable Next.js static assets: stale-while-revalidate
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(request).then((cached) => {
        const networkFetch = fetch(request).then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        });
        return cached || networkFetch;
      }),
    ),
  );
});
