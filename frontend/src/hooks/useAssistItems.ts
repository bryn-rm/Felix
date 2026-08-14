"use client";

import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import type { AssistItem } from "@/lib/types";

interface AssistListResponse {
  items: AssistItem[];
}

/**
 * Live-assist cards for one meeting: the REST list (reconnect/refresh replay
 * source) merged with items pushed live over the meeting WebSocket, deduped by
 * id. Dismissal is optimistic — the card hides immediately, then the REST call
 * persists it (which also logs the engagement signal).
 *
 * `enabled` gates the fetch: when live_assist_mode is off the endpoint 404s
 * (fail closed), so we never even ask.
 */
export function useAssistItems(
  meetingId: string | null,
  liveItems: AssistItem[],
  { enabled = true }: { enabled?: boolean } = {},
) {
  const { data, mutate } = useSWR<AssistListResponse>(
    enabled && meetingId ? `/meetings/${meetingId}/assist` : null,
    (url: string) => api.get<AssistListResponse>(url),
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
