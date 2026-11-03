"use client";

import { useRouter } from "next/navigation";
import useSWR, { useSWRConfig } from "swr";
import { AlertTriangle } from "lucide-react";
import { useGoogleDisconnected } from "@/lib/google-connection-status";
import { supabase } from "@/lib/supabase";
import { clearAllSWR } from "@/components/auth/AuthSync";
import { GoogleReconnectButton } from "@/components/auth/GoogleReconnectButton";
import { api } from "@/lib/api";

export function GoogleDisconnectedBanner() {
  const disconnected = useGoogleDisconnected();
  const router = useRouter();
  const { mutate } = useSWRConfig();
  // Also discover expiry detected by background sync, and restore the warning
  // after reload. Shares the Settings status cache and revalidates on focus.
  useSWR("/auth/google/status", (url: string) => api.get(url), {
    refreshInterval: 60000,
  });

  if (!disconnected) return null;

  async function handleSignOut() {
    await clearAllSWR(mutate);
    await supabase.auth.signOut();
    router.push("/login");
  }

  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-100 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-300" />
        <span>
          Google access is unavailable. Reconnect to resume syncing your inbox and
          calendar.
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <GoogleReconnectButton />
        <button
          onClick={handleSignOut}
          className="rounded border border-amber-400/40 px-3 py-1 text-xs font-medium text-amber-100 transition-colors hover:bg-amber-400/10"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
