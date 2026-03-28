"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { FollowUp } from "@/lib/types";

export type FollowUpFilter = "all" | "waiting" | "overdue" | "closed";

export interface FollowUpCounts {
  all: number;
  waiting: number;
  overdue: number;
  closed: number;
}

function isOverdue(fu: FollowUp): boolean {
  if (fu.status === "closed") return false;
  if (!fu.follow_up_by) return false;
  return new Date(fu.follow_up_by) < new Date();
}

export function useFollowUps(filter: FollowUpFilter = "all") {
  const { data, error, isLoading, mutate } = useSWR<FollowUp[]>(
    "/follow-ups/",
    (url: string) => api.get<FollowUp[]>(url),
    { refreshInterval: 5 * 60 * 1000 },
  );

  const all = data ?? [];

  const followUps =
    filter === "all"
      ? all
      : filter === "overdue"
        ? all.filter(isOverdue)
        : all.filter((fu) => fu.status === filter);

  const counts: FollowUpCounts = {
    all: all.length,
    waiting: all.filter((fu) => fu.status === "waiting" && !isOverdue(fu)).length,
    overdue: all.filter(isOverdue).length,
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
