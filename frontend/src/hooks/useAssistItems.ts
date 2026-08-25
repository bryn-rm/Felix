"use client";

import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import type { AssistItem } from "@/lib/types";

interface AssistListResponse {
  items: AssistItem[];
}

/**
 * How often the capturing device re-reads persisted assist state while the
 * meeting is open.
 *
 * This is reconciliation, not delivery. Everything this device causes still
 * arrives over its WebSocket the moment it happens — proactive cards and its
 * own typed answers — and nothing here is allowed to replace that. What the
 * socket cannot carry is what the OTHER device did: an answer the phone asked
 * for, or a card it dismissed, both of which are only ever written to the
 * database. So this closes that one gap and no more.
 *
 * Fifteen seconds, not the viewer's three: the phone is watching a live stream
 * of cards and is the device someone is actively reading, while this one is
 * catching up on the occasional out-of-band change and already has the
 * transcript socket open. Matching the phone's cadence would multiply a
 * whole-list read for no perceptible gain.
 */
export const ASSIST_RECONCILE_MS = 15_000;

/**
 * Live-assist cards for one meeting: the REST list (reconnect/refresh replay
 * source, and the reconciliation source for changes made on another device)
 * merged with items pushed live over the meeting WebSocket, deduped by id.
 * Dismissal is optimistic — the card hides immediately, then the REST call
 * persists it (which also logs the engagement signal).
 *
 * Persisted rows win the id dedupe, so this is server-authoritative in both
 * directions: a card the phone dismissed comes back `dismissed: true` and
 * disappears here even though the socket delivered it, and a phone-originated
 * answer appears here without ever having touched this device's socket.
 *
 * `enabled` gates the fetch: when live_assist_mode is off the endpoint 404s
 * (fail closed), so we never even ask. `live` gates the revalidation: an ended
 * meeting has nothing left to reconcile, so the polling stops with it.
 */
export function useAssistItems(
  meetingId: string | null,
  liveItems: AssistItem[],
  { enabled = true, live = false }: { enabled?: boolean; live?: boolean } = {},
) {
  const { data, mutate } = useSWR<AssistListResponse>(
    enabled && meetingId ? `/meetings/${meetingId}/assist` : null,
    (url: string) => api.get<AssistListResponse>(url),
    { refreshInterval: live ? ASSIST_RECONCILE_MS : 0 },
  );
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const items = useMemo(() => {
    const merged = new Map<string, AssistItem>();
    for (const item of data?.items ?? []) merged.set(item.id, item);
    for (const item of liveItems) {
      if (!merged.has(item.id)) merged.set(item.id, item);
    }
    return [...merged.values()].filter(
      (item) => !item.dismissed && !hidden.has(item.id),
    );
  }, [data?.items, liveItems, hidden]);

  const dismiss = useCallback(
    async (itemId: string) => {
      setHidden((prev) => new Set(prev).add(itemId));
      try {
        await api.post(`/meetings/${meetingId}/assist/${itemId}/dismiss`, {});
        await mutate();
      } catch {
        // Persisting the dismissal failed — undo the optimistic hide so the
        // card doesn't silently reappear on the next refresh.
        setHidden((prev) => {
          const next = new Set(prev);
          next.delete(itemId);
          return next;
        });
      }
    },
    [meetingId, mutate],
  );

  return { items, dismiss };
}
