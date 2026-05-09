"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { useSessionReady } from "@/hooks/useSessionReady";
import { inboxDebug } from "@/lib/inbox-debug";
import { authAwareRetry } from "@/lib/swr-auth-retry";

interface EmailCounts {
  action_required: number;
  overdue_followups: number;
}

async function fetchCounts(): Promise<EmailCounts> {
  inboxDebug("swr:fire", { url: "/emails/counts" });
  return api.get<EmailCounts>("/emails/counts", { skipAuthRedirect: true });
}

export function useUnreadCounts() {
  const ready = useSessionReady();
  const { data, error } = useSWR<EmailCounts>(
    ready ? "emails-counts" : null,
    fetchCounts,
    {
      refreshInterval: 2 * 60 * 1000, // 2 minutes
      fallbackData: { action_required: 0, overdue_followups: 0 },
      onErrorRetry: authAwareRetry,
    },
  );

  return {
    actionRequired: data?.action_required ?? 0,
    overdueFollowups: data?.overdue_followups ?? 0,
    error,
  };
}
