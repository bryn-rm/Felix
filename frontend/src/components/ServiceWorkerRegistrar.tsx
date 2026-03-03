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
      navigator.serviceWorker
        .register("/sw.js")
        .then((reg) => {
          console.log("[Felix] Service worker registered:", reg.scope);
        })
        .catch((err) => {
          console.warn("[Felix] Service worker registration failed:", err);
        });
    }
  }, []);

  return null;
}
