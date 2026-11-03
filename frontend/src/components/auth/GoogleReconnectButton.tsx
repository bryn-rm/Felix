"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { safeReturnPath } from "@/lib/return-to";

/** Renew Google authorization independently of the Felix sign-in session. */
export function GoogleReconnectButton() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reconnect() {
    setLoading(true);
    setError(null);
    try {
      const returnTo = safeReturnPath(`${window.location.pathname}${window.location.search}`) ?? "/home";
      const { auth_url } = await api.get<{ auth_url: string }>(
        `/auth/google/connect?next=${encodeURIComponent(returnTo)}`,
        { signal: AbortSignal.timeout(15000) },
      );
      window.location.href = auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reconnect Google. Please try again.");
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={reconnect}
        disabled={loading}
        className="rounded bg-amber-400 px-3 py-1 text-xs font-semibold text-slate-900 transition-colors hover:bg-amber-300 disabled:cursor-wait disabled:opacity-60"
      >
        {loading ? "Redirecting to Google…" : "Reconnect Google"}
      </button>
      {error && <p role="alert" className="max-w-sm text-xs text-red-300">{error}</p>}
    </div>
  );
}
