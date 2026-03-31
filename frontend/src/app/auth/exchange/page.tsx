"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function ExchangePage() {
  const router = useRouter();

  useEffect(() => {
    // Implicit flow delivers the session in the URL hash (#access_token=...).
    // The Supabase client parses the hash automatically when onAuthStateChange
    // fires, storing the session in localStorage via our configured storage adapter.
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_IN" && session) {
        subscription.unsubscribe();
        router.replace("/connect");
      }
    });

    // Handle the case where the session is already available (e.g. page refresh)
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        subscription.unsubscribe();
        router.replace("/connect");
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, [router]);

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
