"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { api } from "@/lib/api";

export default function CallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function handleCallback() {
      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");

      if (code) {
        const { error } = await supabase.auth.exchangeCodeForSession(code);
        if (error) {
          setError(error.message);
          return;
        }
      }

      const { data: { session }, error } = await supabase.auth.getSession();

      if (error || !session) {
        setError(error?.message ?? "Sign-in failed");
        return;
      }

      // Check whether the user already has a connected Google account
      try {
        const status = await api.get<{ connected: boolean }>("/auth/google/status");
        router.replace(status.connected ? "/dashboard" : "/connect");
      } catch {
        // If the status check fails, send to /connect as a safe default
        router.replace("/connect");
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
