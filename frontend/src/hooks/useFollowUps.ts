"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { isOverdue } from "@/lib/follow-ups";
import type { FollowUp } from "@/lib/types";

export type FollowUpFilter = "all" | "waiting" | "overdue" | "closed";

export interface FollowUpCounts {
  all: number;
  waiting: number;
  overdue: number;
  closed: number;
}

export function useFollowUps(filter: FollowUpFilter = "all") {
  const { data, error, isLoading, mutate } = useSWR<{ follow_ups: FollowUp[]; count: number }>(
    "/follow-ups/",
    (url: string) => api.get<{ follow_ups: FollowUp[]; count: number }>(url),
    { refreshInterval: 5 * 60 * 1000 },
  );

  const all = data?.follow_ups ?? [];

  const followUps =
    filter === "all"
      ? all
      : filter === "overdue"
        ? all.filter((fu) => isOverdue(fu))
        : all.filter((fu) => fu.status === filter);

  const counts: FollowUpCounts = {
    all: all.length,
    waiting: all.filter((fu) => fu.status === "waiting" && !isOverdue(fu)).length,
    overdue: all.filter((fu) => isOverdue(fu)).length,
    closed: all.filter((fu) => fu.status === "closed").length,
  };

  return {
    followUps,
    counts,
    isLoading,
    error: error as Error | undefined,
    mutate,
  };
}
