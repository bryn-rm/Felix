"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { LiveAssistView } from "@/lib/types";

/**
 * How often the viewer re-reads persisted assist state while the meeting is
 * still live. Cards are written by the laptop's watcher at most every
 * MIN_CARD_INTERVAL_S (20s) apart, so this is well inside "feels immediate"
 * without being a busy loop on someone's phone battery.
 */
export const VIEWER_POLL_MS = 3000;

/**
 * Read-only Live Assist state for one meeting — the phone/second-screen hook.
 *
 * Reads only. There is deliberately no mutation here (no dismiss, no ask, no
 * lifecycle): the device that started the meeting owns capture, STT and assist
 * generation, and this hook exists purely to replay what that device has
 * already persisted.
 *
 * Polling stops the moment the meeting leaves `recording` — same shape as
 * `useMeeting`'s processing poll — so a finished meeting can't leave a phone
 * polling in a pocket. The card list is server-filtered to undismissed items,
 * which is also how a dismissal on the laptop reaches the phone.
 */
export function useLiveAssistViewer(meetingId: string | null) {
  const { data, error, isLoading } = useSWR<LiveAssistView>(
    meetingId ? `/meetings/${meetingId}/live-view` : null,
    (url: string) => api.get<LiveAssistView>(url),
    {
      refreshInterval: (latest) =>
        latest?.meeting?.status === "recording" ? VIEWER_POLL_MS : 0,
    },
  );

  return {
    meeting: data?.meeting,
    items: data?.items ?? [],
    /** The session is still open on the capturing device. */
    live: data?.meeting?.status === "recording",
    isLoading,
    error: error as Error | undefined,
  };
}
