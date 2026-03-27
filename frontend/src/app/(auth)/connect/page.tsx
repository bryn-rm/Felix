"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export default function ConnectPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConnect() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<{ auth_url: string }>("/auth/google/connect");
      window.location.href = data.auth_url;
      // Browser navigates away — no need to reset loading
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-[#0f172a]">
      <div className="flex flex-col items-center gap-8 px-6 py-12 rounded-2xl bg-[#1e293b] shadow-2xl w-full max-w-md">
        {/* Icon */}
        <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-indigo-600/20 border border-indigo-500/30">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="#4f46e5"
            strokeWidth={1.8}
            strokeLinecap="round"
            strokeLinejoin="round"
            width={28}
            height={28}
            aria-hidden
          >
            <rect width="20" height="16" x="2" y="4" rx="2" />
            <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
          </svg>
        </div>

        <div className="text-center">
          <h1 className="text-xl font-bold text-[#f1f5f9]">
            Connect Gmail &amp; Calendar
          </h1>
          <p className="mt-2 text-sm text-[#94a3b8] leading-relaxed">
            Felix needs access to your Gmail and Google Calendar to work as your
            chief of staff.
          </p>
        </div>

        {/* Permission list */}
        <ul className="w-full space-y-3 text-sm">
          {[
            ["Read emails", "To triage your inbox and surface what matters"],
            ["Send emails", "To send AI-drafted replies on your behalf"],
            ["Read calendar", "To show your day and avoid scheduling conflicts"],
          ].map(([title, desc]) => (
            <li key={title} className="flex items-start gap-3">
              <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center">
                <svg
                  viewBox="0 0 12 12"
                  width={10}
                  height={10}
                  fill="none"
                  stroke="#6366f1"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <polyline points="2 6 5 9 10 3" />
                </svg>
              </span>
              <span>
                <span className="font-medium text-[#f1f5f9]">{title}</span>
                <span className="text-[#94a3b8]"> — {desc}</span>
              </span>
            </li>
          ))}
        </ul>

        {error && (
          <p className="w-full rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        <button
          onClick={handleConnect}
          disabled={loading}
          className="flex items-center justify-center gap-2 w-full rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 disabled:cursor-wait transition-colors px-5 py-3 text-sm font-semibold text-white shadow"
        >
          {loading ? (
            <>
              <svg
                className="animate-spin"
                width={16}
                height={16}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden
              >
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
              Redirecting to Google…
            </>
          ) : (
            "Connect Gmail & Calendar"
          )}
        </button>

        <p className="text-xs text-[#94a3b8] text-center">
          You can revoke access at any time from your Google account settings.
        </p>
      </div>
    </main>
  );
}
