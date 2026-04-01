"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSignIn() {
    setLoading(true);
    setError(null);
    const appUrl =
      process.env.NEXT_PUBLIC_APP_URL?.replace(/\/$/, "") ??
      window.location.origin;
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
    <main className="min-h-screen flex items-center justify-center bg-[#0f172a]">
      <div className="flex flex-col items-center gap-8 px-6 py-12 rounded-2xl bg-[#1e293b] shadow-2xl w-full max-w-sm">
        {/* Felix logo — indigo squircle with white F */}
        <div
          className="flex items-center justify-center rounded-[22px] bg-indigo-600 shadow-lg shadow-indigo-500/30"
          style={{ width: 72, height: 72 }}
        >
          <svg
            viewBox="0 0 40 40"
            width={36}
            height={36}
            fill="none"
            aria-label="Felix"
          >
            <text
              x="50%"
              y="50%"
              dominantBaseline="central"
              textAnchor="middle"
              fill="white"
              fontSize="26"
              fontWeight="700"
              fontFamily="system-ui, sans-serif"
            >
              F
            </text>
          </svg>
        </div>

        <div className="text-center">
          <h1 className="text-2xl font-bold text-[#f1f5f9] tracking-tight">
            Felix
          </h1>
          <p className="mt-1 text-sm text-[#94a3b8]">
            Your AI chief of staff
          </p>
        </div>

        {error && (
          <p className="w-full rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        <button
          onClick={handleSignIn}
          disabled={loading}
          className="flex items-center justify-center gap-3 w-full rounded-xl bg-white hover:bg-gray-100 disabled:opacity-60 disabled:cursor-wait transition-colors px-5 py-3 text-sm font-semibold text-gray-800 shadow"
        >
          {/* Google "G" icon */}
          <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden>
            <path
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
              fill="#4285F4"
            />
            <path
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              fill="#34A853"
            />
            <path
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"
              fill="#FBBC05"
            />
            <path
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              fill="#EA4335"
            />
          </svg>
          {loading ? "Redirecting…" : "Sign in with Google"}
        </button>

        <p className="text-xs text-[#94a3b8] text-center">
          By signing in you agree to let Felix access your Gmail and Calendar to
          act as your chief of staff.
        </p>
      </div>
    </main>
  );
}
