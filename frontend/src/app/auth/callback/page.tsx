"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { api } from "@/lib/api";

export default function CallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    console.log("[callback] component mounted, URL:", window.location.href);

    async function navigateWithSession(session: { access_token: string }) {
      console.log("[callback] session acquired, navigating...", session.access_token.slice(0, 10) + "...");
      try {
        const status = await api.get<{ connected: boolean }>("/auth/google/status");
        router.replace(status.connected ? "/dashboard" : "/connect");
      } catch {
        router.replace("/connect");
      }
    }

    async function handleCallback() {
      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");

      if (code) {
        console.log("[callback] found code in URL, exchanging...", code.slice(0, 10) + "...");
        const { data, error } = await supabase.auth.exchangeCodeForSession(code);
        console.log("[callback] exchangeCodeForSession result — session:", !!data.session, "error:", error?.message ?? "none");

        if (error) {
          setError(error.message);
          return;
        }

        if (data.session) {
          await navigateWithSession(data.session);
          return;
        }
      }

      // Fallback: no code in URL, check for existing session
      console.log("[callback] no code in URL, trying getSession() fallback");
      const { data: { session }, error } = await supabase.auth.getSession();
      console.log("[callback] getSession() result — session:", !!session, "error:", error?.message ?? "none");

      if (session) {
        await navigateWithSession(session);
      } else {
        setError(error?.message ?? "Sign in failed. Please try again.");
      }
    }

    handleCallback();
  }, [router]);

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-[#0f172a]">
        <div className="rounded-2xl bg-[#1e293b] px-8 py-10 max-w-sm w-full text-center shadow-2xl">
          <p className="text-red-400 font-medium mb-4">Sign-in failed</p>
          <p className="text-sm text-[#94a3b8] mb-6">{error}</p>
          <a
            href="/login"
            className="inline-block rounded-xl bg-indigo-600 hover:bg-indigo-500 px-5 py-2.5 text-sm font-semibold text-white transition-colors"
          >
            Back to sign in
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-[#0f172a]">
      <div className="flex flex-col items-center gap-4">
        <svg
          className="animate-spin text-indigo-500"
          width={32}
          height={32}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          aria-label="Loading"
        >
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
        <p className="text-sm text-[#94a3b8]">Signing you in…</p>
      </div>
    </main>
  );
}
