"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";

const features = [
  "Triages your inbox so you focus on what matters",
  "Drafts replies in your voice",
  "Briefs you every morning on your day ahead",
];

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSignIn() {
    setLoading(true);
    setError(null);
    // Always use the live browser origin for OAuth callbacks.
    // In GitHub Codespaces, hostnames can change between restarts; relying on
    // a static env var often causes Supabase to redirect to an old hostname.
    const appUrl = window.location.origin.replace(/\/$/, "");
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${appUrl}/auth/callback`,
      },
    });
    if (error) {
      setError(error.message);
      setLoading(false);
    }
    // On success the browser navigates away — no need to reset loading
  }

  return (
    <main className="min-h-screen flex flex-col lg:flex-row bg-[#0f172a]">
      {/* ── LEFT PANEL ── */}
      <div className="relative flex flex-col justify-center px-8 py-14 lg:py-0 lg:px-16 lg:w-[60%] overflow-hidden">
        {/* Soft radial glow — bottom-left */}
        <div className="pointer-events-none absolute -bottom-40 -left-40 w-[600px] h-[600px] rounded-full bg-indigo-600/10 blur-3xl" />
        {/* Dot grid */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.06]"
          style={{
            backgroundImage:
              "radial-gradient(circle, #818cf8 1px, transparent 1px)",
            backgroundSize: "28px 28px",
          }}
        />

        <div className="relative z-10 max-w-lg">
          {/* Logo + wordmark */}
          <div className="flex items-center gap-4 mb-10">
            <img
              src="/icon-512.png"
              alt="Felix icon"
              width={64}
              height={64}
              className="rounded-2xl shadow-lg shadow-indigo-500/20 flex-shrink-0"
            />
            <span className="text-[2.5rem] font-bold text-[#f1f5f9] tracking-tight leading-none">
              Felix
            </span>
          </div>

          {/* Tagline */}
          <h1 className="text-3xl lg:text-[2.6rem] font-bold text-[#f1f5f9] leading-tight mb-4">
            Your AI chief of staff for email and calendar
          </h1>
          <p className="text-[#94a3b8] text-lg mb-10 leading-relaxed">
            Stop drowning in email. Start getting things done.
          </p>

          {/* Feature highlights */}
          <ul className="space-y-5">
            {features.map((feature) => (
              <li key={feature} className="flex items-start gap-3.5">
                <span className="mt-0.5 flex-shrink-0 w-6 h-6 rounded-full bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center">
                  <svg
                    viewBox="0 0 12 12"
                    width={10}
                    height={10}
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
                <span className="text-[#cbd5e1] text-base leading-snug">
                  {feature}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* ── RIGHT PANEL ── */}
      <div className="flex items-center justify-center px-6 py-10 lg:py-0 lg:w-[40%]">
        <div className="w-full max-w-sm">
          <div className="rounded-2xl bg-[#1e293b] border border-white/[0.06] shadow-2xl px-8 py-10">
            <h2 className="text-2xl font-bold text-[#f1f5f9] mb-1">
              Welcome back
            </h2>
            <p className="text-sm text-[#94a3b8] mb-8">
              Sign in to continue to Felix
            </p>

            {error && (
              <p className="mb-6 rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
                {error}
              </p>
            )}

            <button
              onClick={handleSignIn}
              disabled={loading}
              className="flex items-center justify-center gap-3 w-full rounded-xl bg-white hover:bg-gray-50 disabled:opacity-60 disabled:cursor-wait px-5 py-3.5 text-sm font-semibold text-gray-800 shadow-md transition-all duration-150 hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0"
            >
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
                <path
                  fill="#4285F4"
                  d="M16.51 8H8.98v3h4.3c-.18 1-.74 1.48-1.6 2.04v2.01h2.6a7.8 7.8 0 0 0 2.38-5.88c0-.57-.05-.66-.15-1.18z"
                />
                <path
                  fill="#34A853"
                  d="M8.98 17c2.16 0 3.97-.72 5.3-1.94l-2.6-2a4.8 4.8 0 0 1-7.18-2.54H1.83v2.07A8 8 0 0 0 8.98 17z"
                />
                <path
                  fill="#FBBC05"
                  d="M4.5 10.52a4.8 4.8 0 0 1 0-3.04V5.41H1.83a8 8 0 0 0 0 7.18l2.67-2.07z"
                />
                <path
                  fill="#EA4335"
                  d="M8.98 4.18c1.17 0 2.23.4 3.06 1.2l2.3-2.3A8 8 0 0 0 1.83 5.4L4.5 7.49a4.77 4.77 0 0 1 4.48-3.31z"
                />
              </svg>
              {loading ? "Redirecting…" : "Continue with Google"}
            </button>

            <p className="mt-6 text-xs text-[#475569] text-center">
              By signing in you agree to Felix being awesome
            </p>

            <div className="mt-6 pt-6 border-t border-white/[0.06]">
              <p className="text-xs text-[#475569] text-center leading-relaxed">
                Your data stays private. Felix connects to your Google account
                with read and send permissions only.
              </p>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
