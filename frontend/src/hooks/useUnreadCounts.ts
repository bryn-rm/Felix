"use client";

import useSWR from "swr";
import { api } from "@/lib/api";

interface EmailCounts {
  action_required: number;
  overdue_followups: number;
}

async function fetchCounts(): Promise<EmailCounts> {
  return api.get<EmailCounts>("/emails/counts");
}

export function useUnreadCounts() {
  const { data, error } = useSWR<EmailCounts>("emails-counts", fetchCounts, {
    refreshInterval: 2 * 60 * 1000, // 2 minutes
    fallbackData: { action_required: 0, overdue_followups: 0 },
  });

  return {
    actionRequired: data?.action_required ?? 0,
    overdueFollowups: data?.overdue_followups ?? 0,
    error,
  };
}
