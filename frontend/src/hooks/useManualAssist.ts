"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { AssistItem } from "@/lib/types";
import type { AssistAskOptions } from "@/hooks/useMeetingCapture";

/**
 * Deadline on one REST ask, mirroring useMeetingCapture.ASK_TIMEOUT_MS. `fetch`
 * never settles on its own if the request stalls (proxy drop, cold-start hang,
 * sleeping laptop), which would leave askPending stuck true and the ask box
 * disabled until a full page reload. Kept above the server's CALL_TIMEOUT_S so
 * the server's own correlated error wins the race in the normal case.
 */
const ASK_TIMEOUT_MS = 75_000;

/** Typed assist transport for a manual session, where no capture WebSocket exists. */
export function useManualAssist(meetingId: string, enabled: boolean) {
  const [items, setItems] = useState<AssistItem[]>([]);
  const [askPending, setAskPending] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  // The in-flight guard is a ref, not the state above: it must be readable
  // synchronously by a second call in the same tick, before React re-renders.
  const pendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const sendAsk = useCallback(
    async (question: string, options: AssistAskOptions = {}): Promise<boolean> => {
      const trimmed = question.trim();
      if (!enabled || !trimmed || pendingRef.current) return false;

      const requestId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `ask-${Date.now()}`;
      pendingRef.current = true;
      setAskPending(true);
      setAskError(null);

      const controller = new AbortController();
      abortRef.current = controller;
      const timer = setTimeout(() => controller.abort(), ASK_TIMEOUT_MS);
      try {
        const { item } = await api.post<{ item: AssistItem }>(
          `/meetings/${meetingId}/assist/ask`,
          {
            question: trimmed,
            request_id: requestId,
            intent: options.intent ?? "answer",
            parent_item_id: options.parentItemId ?? null,
            focus: options.focus ?? null,
          },
          { signal: controller.signal },
        );
        setItems((current) =>
          current.some((existing) => existing.id === item.id)
            ? current
            : [...current, item],
        );
        return true;
      } catch (error: unknown) {
        setAskError(
          controller.signal.aborted
            ? "No answer arrived — try asking again."
            : error instanceof ApiError
              ? error.message
              : "Couldn’t answer that just now — try again.",
        );
        return false;
      } finally {
        clearTimeout(timer);
        if (abortRef.current === controller) abortRef.current = null;
        pendingRef.current = false;
        setAskPending(false);
      }
    },
    [enabled, meetingId],
  );

  return { items, sendAsk, askPending, askError };
}
