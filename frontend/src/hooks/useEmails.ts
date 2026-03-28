"use client";

import useSWRInfinite from "swr/infinite";
import { api } from "@/lib/api";
import type { Email } from "@/lib/types";

interface EmailsPage {
  emails: Email[];
  total: number;
}

interface UseEmailsOptions {
  category?: string;
  limit?: number;
}

export function useEmails({ category, limit = 20 }: UseEmailsOptions = {}) {
  const getKey = (pageIndex: number, prev: EmailsPage | null) => {
    // Stop fetching when the last page had fewer items than the limit
    if (prev && prev.emails.length < limit) return null;
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    params.set("limit", String(limit));
    params.set("offset", String(pageIndex * limit));
    return `/emails/?${params.toString()}`;
  };

  const { data, error, isLoading, size, setSize } = useSWRInfinite<EmailsPage>(
    getKey,
    (url: string) => api.get<EmailsPage>(url),
    { refreshInterval: 2 * 60 * 1000 },
  );

  const emails: Email[] = data ? data.flatMap((page) => page.emails) : [];
  const total: number = data?.[0]?.total ?? 0;

  return {
    emails,
    total,
    isLoading,
    error: error as Error | undefined,
    hasMore: emails.length < total,
    loadMore: () => setSize(size + 1),
  };
}
