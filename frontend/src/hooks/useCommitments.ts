"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { Commitment } from "@/lib/types";

export type CommitmentDirection = "owed_by_user" | "owed_to_user" | "all";
export type CommitmentStatus = "open" | "done" | "dropped" | "rescued";

interface ListResponse {
  commitments: Commitment[];
  count: number;
}

function buildPath(direction: CommitmentDirection, status: CommitmentStatus): string {
  const params = new URLSearchParams();
  if (direction !== "all") params.set("direction", direction);
  params.set("status", status);
  const qs = params.toString();
  return `/commitments${qs ? `?${qs}` : ""}`;
}

export function useCommitments(
  direction: CommitmentDirection = "all",
  status: CommitmentStatus = "open",
) {
  const path = buildPath(direction, status);
  const { data, error, isLoading, mutate } = useSWR<ListResponse>(
    path,
    (url: string) => api.get<ListResponse>(url),
    { refreshInterval: 5 * 60 * 1000 },
  );

  async function resolve(id: string, status: "done" | "dropped" | "rescued" = "done") {
    await api.post(`/commitments/${id}/resolve`, { status });
    await mutate();
  }

  return {
    commitments: data?.commitments ?? [],
    count: data?.count ?? 0,
    isLoading,
    error: error as Error | undefined,
    mutate,
    resolve,
  };
}
