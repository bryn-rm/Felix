"use client";

import useSWRInfinite from "swr/infinite";
import { api } from "@/lib/api";
import { useSessionReady } from "@/hooks/useSessionReady";
import { inboxDebug } from "@/lib/inbox-debug";
import { authAwareRetry } from "@/lib/swr-auth-retry";
import type { Email } from "@/lib/types";

interface EmailsPage {
  emails: Email[];
  total: number;
}

interface UseEmailsOptions {
  category?: string;
  search?: string;
  limit?: number;
}

export function useEmails({ category, search = "", limit = 20 }: UseEmailsOptions = {}) {
  const ready = useSessionReady();
  const trimmedSearch = search.trim();

  const getKey = (pageIndex: number, prev: EmailsPage | null) => {
    if (!ready) return null;
    // Stop fetching when the last page had fewer items than the limit
    if (prev && prev.emails.length < limit) return null;
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (trimmedSearch) params.set("search", trimmedSearch);
    params.set("limit", String(limit));
    params.set("offset", String(pageIndex * limit));
    return `/emails/?${params.toString()}`;
  };

  const { data, error, isLoading, size, setSize } = useSWRInfinite<EmailsPage>(
    getKey,
    (url: string) => {
      inboxDebug("swr:fire", { url });
      return api.get<EmailsPage>(url, { skipAuthRedirect: true });
    },
    {
      refreshInterval: 2 * 60 * 1000,
      onErrorRetry: authAwareRetry,
    },
  );

  const emails: Email[] = data ? data.flatMap((page) => page.emails) : [];
  const total: number = data?.[0]?.total ?? 0;

  return {
    emails,
    total,
    isLoading: isLoading || !ready,
    error: error as Error | undefined,
    hasMore: emails.length < total,
    loadMore: () => setSize(size + 1),
  };
}
