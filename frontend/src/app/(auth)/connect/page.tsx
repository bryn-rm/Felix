"use client";

import { useState } from "react";
import { api } from "@/lib/api";

const permissions = [
  {
    title: "Read emails",
    desc: "To triage your inbox and surface what matters",
  },
  {
    title: "Send emails",
    desc: "To send AI-drafted replies on your behalf",
  },
  {
    title: "Read calendar",
    desc: "To show your day and avoid scheduling conflicts",
  },
];

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
    <main className="min-h-screen flex items-center justify-center bg-[#0f172a] px-6 py-12">
      {/* Ambient glow */}
      <div className="pointer-events-none fixed bottom-0 left-1/2 -translate-x-1/2 w-[700px] h-[400px] rounded-full bg-indigo-600/8 blur-3xl" />

      <div className="relative z-10 w-full max-w-md">
        {/* Header */}
        <div className="flex items-center gap-3 mb-8">
          <img
            src="/icon-512.png"
            alt="Felix icon"
            width={36}
            height={36}
            className="rounded-xl shadow-md shadow-indigo-500/20 flex-shrink-0"
          />
          <span className="text-lg font-bold text-[#f1f5f9] tracking-tight">
            Felix
          </span>
        </div>

        <div className="rounded-2xl bg-[#1e293b] border border-white/[0.06] shadow-2xl px-8 py-10">
          {/* Icon */}
          <div className="mb-6 w-12 h-12 rounded-xl bg-indigo-600/15 border border-indigo-500/25 flex items-center justify-center">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="#818cf8"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
              width={22}
              height={22}
              aria-hidden
            >
              <rect width="20" height="16" x="2" y="4" rx="2" />
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
            </svg>
          </div>

          <h1 className="text-xl font-bold text-[#f1f5f9] mb-2">
            Connect Gmail &amp; Calendar
          </h1>
          <p className="text-sm text-[#94a3b8] leading-relaxed mb-8">
            Felix needs access to your Gmail and Google Calendar to work as your
            chief of staff.
          </p>

          {/* Permission list */}
          <ul className="space-y-4 mb-8">
            {permissions.map(({ title, desc }) => (
              <li key={title} className="flex items-start gap-3">
                <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center">
                  <svg
                    viewBox="0 0 12 12"
                    width={9}
                    height={9}
                    fill="none"
                    stroke="#818cf8"
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden
                  >
                    <polyline points="2 6 5 9 10 3" />
                  </svg>
                </span>
                <span className="text-sm leading-snug">
                  <span className="font-medium text-[#e2e8f0]">{title}</span>
                  <span className="text-[#64748b]"> — {desc}</span>
                </span>
              </li>
            ))}
          </ul>

          {error && (
            <p className="mb-6 rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
              {error}
            </p>
          )}

          <button
            onClick={handleConnect}
            disabled={loading}
            className="flex items-center justify-center gap-2 w-full rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 disabled:cursor-wait transition-all duration-150 px-5 py-3.5 text-sm font-semibold text-white shadow-lg shadow-indigo-600/25 hover:shadow-indigo-500/30 hover:-translate-y-0.5 active:translate-y-0"
          >
            {loading ? (
              <>
                <svg
                  className="animate-spin"
                  width={15}
                  height={15}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2.5}
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

          <div className="mt-6 pt-6 border-t border-white/[0.06]">
            <p className="text-xs text-[#475569] text-center leading-relaxed">
              You can revoke access at any time from your Google account
              settings.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
