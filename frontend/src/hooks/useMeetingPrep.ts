"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { MeetingPrep } from "@/lib/types";

interface NextPrepResponse {
  prep: MeetingPrep | null;
}

/**
 * Polls /meetings/next-prep for the user's next upcoming meeting prep card.
 *
 * Cadence:
 *   - 60s while a prep card exists and the meeting is within 30 minutes
 *   - 5min otherwise
 */
export function useNextMeetingPrep() {
  const { data, error, isLoading, mutate } = useSWR<NextPrepResponse>(
    "/meetings/next-prep",
    (url: string) => api.get<NextPrepResponse>(url),
    {
      refreshInterval: (latest) => {
        const prep = latest?.prep ?? null;
        if (!prep || !prep.event_start) return 5 * 60 * 1000;
        const start = new Date(prep.event_start).getTime();
        const minsUntil = (start - Date.now()) / 60_000;
        if (minsUntil > 0 && minsUntil <= 30) return 60_000;
        return 5 * 60 * 1000;
      },
      revalidateOnFocus: false,
    },
  );

  return {
    prep: data?.prep ?? null,
    isLoading,
    error: error as Error | undefined,
    mutate,
  };
}
