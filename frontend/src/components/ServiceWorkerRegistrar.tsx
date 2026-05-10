"use client";

import { useEffect } from "react";

/**
 * Thin client component whose only job is to register the service worker.
 * Must be a Client Component (useEffect), but isolated here so the root
 * layout can stay a Server Component.
 */
export default function ServiceWorkerRegistrar() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "";
      const swUrl = apiBase
        ? `/sw.js?apiBase=${encodeURIComponent(apiBase)}`
        : "/sw.js";

      navigator.serviceWorker
        .register(swUrl)
        .then((reg) => {
          if (process.env.NODE_ENV === "development") {
            console.log("[Felix] Service worker registered:", reg.scope);
          }
        })
        .catch((err) => {
          console.warn("[Felix] Service worker registration failed:", err);
        });
    }
  }, []);

  return null;
}
