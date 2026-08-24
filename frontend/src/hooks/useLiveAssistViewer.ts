"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useAssistAsk } from "@/hooks/useAssistAsk";
import type { AssistItem, LiveAssistView } from "@/lib/types";

/**
 * How often the viewer re-reads persisted assist state while the meeting is
 * still live. Cards are written by the capturing device's watcher at most every
 * MIN_CARD_INTERVAL_S (20s) apart, so this is well inside "feels immediate"
 * without being a busy loop on someone's phone battery.
 */
export const VIEWER_POLL_MS = 3000;

/**
 * Live Assist state for one meeting on a second device — the phone hook.
 *
 * Reads persisted state on a short poll and, while the meeting is open, can ask
 * Felix a question over REST. What it still cannot do is capture: no WebSocket,
 * no media, no STT, no watcher. An ask goes to the same server-side
 * implementation the capturing device's asks do, so limits, cooldowns, model,
 * context and persistence are identical — the phone is a second client of the
 * meeting's assistant, never a second owner of the meeting.
 *
 * Polling stops the moment the meeting leaves `recording` — same shape as
 * `useMeeting`'s processing poll — and asking goes with it, because the server
 * refuses an ask on a closed session anyway.
 */
export function useLiveAssistViewer(meetingId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<LiveAssistView>(
    meetingId ? `/meetings/${meetingId}/live-view` : null,
    (url: string) => api.get<LiveAssistView>(url),
    {
      refreshInterval: (latest) =>
        latest?.meeting?.status === "recording" ? VIEWER_POLL_MS : 0,
    },
  );

  const live = data?.meeting?.status === "recording";
  // Answers arrive here before the next poll would carry them, so the card
  // appears the moment it lands rather than up to VIEWER_POLL_MS later.
  const ask = useAssistAsk(meetingId ?? "", Boolean(meetingId) && live);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // Dismissals are per meeting. This hook can outlive one — a client-side
  // navigation between two live meetings reuses the instance — and a stale set
  // would keep filtering the next meeting's cards by the previous one's ids.
  useEffect(() => setHidden(new Set()), [meetingId]);

  const items = useMemo(() => {
    // Server rows win ID dedupe, including dismissed=true. Locally answered
    // items fill only the gap before their first polling snapshot lands.
    const merged = new Map<string, AssistItem>();
    for (const item of data?.items ?? []) {
      merged.set(item.id, item);
    }
    for (const item of ask.items) {
      if (!merged.has(item.id)) {
        merged.set(item.id, item);
      }
    }
    return [...merged.values()].filter(
      (item) => !item.dismissed && !hidden.has(item.id),
    );
  }, [data?.items, ask.items, hidden]);

  const dismiss = useCallback(
    async (itemId: string) => {
      // `meetingId` is nullable and only the SWR key guards on it; without
      // this the URL would interpolate "null", 404, and silently roll the
      // optimistic hide back — a card reappearing with nothing to explain it.
      if (!meetingId) return;
      // Optimistic, then persisted — the same contract as the capture page, and
      // against the same `dismissed` column, so the two devices cannot disagree
      // about what was dismissed. (The capture page picks it up on its next
      // revalidation; nothing here is device-local.)
      setHidden((prev) => new Set(prev).add(itemId));
      try {
        await api.post(`/meetings/${meetingId}/assist/${itemId}/dismiss`, {});
        await mutate();
      } catch {
        setHidden((prev) => {
          const next = new Set(prev);
          next.delete(itemId);
          return next;
        });
      }
    },
    [meetingId, mutate],
  );

  return {
    meeting: data?.meeting,
    items,
    /** The session is still open on the capturing device. */
    live,
    /**
     * Is the capturing device still attached? null when the question doesn't
     * apply (manual session, or nothing loaded yet). False means no NEW
     * proactive cards until it reconnects — asks are unaffected, they never
     * used that socket.
     */
    captureAttached: data ? data.capture_attached : null,
    isLoading,
    error: error as Error | undefined,
    sendAsk: ask.sendAsk,
    askPending: ask.askPending,
    askError: ask.askError,
    dismiss,
  };
}
