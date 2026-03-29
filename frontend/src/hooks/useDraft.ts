"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Draft, Email } from "@/lib/types";

export type DraftState =
  | "loading"    // initial fetch in progress
  | "generating" // streaming tokens from LLM
  | "ready"      // editable by user
  | "sending"    // awaiting send API response
  | "sent"       // successfully sent
  | "error";     // unrecoverable error

interface UseDraftReturn {
  draft: Draft | null;
  draftText: string;
  state: DraftState;
  error: string | null;
  send: (editedText: string) => Promise<void>;
  discard: () => Promise<void>;
}

export function useDraft(emailId: string): UseDraftReturn {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftText, setDraftText] = useState("");
  const [state, setState] = useState<DraftState>("loading");
  const [error, setError] = useState<string | null>(null);

  // Track original generated text for edit-detection on send
  const originalTextRef = useRef<string>("");
  // Allow aborting stream on unmount
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);

  // ------------------------------------------------------------------
  // Start streaming generation
  // ------------------------------------------------------------------
  const startStream = useCallback(async () => {
    setState("generating");
    setDraftText("");

    try {
      const stream = await api.streamDraft(emailId);
      const reader = stream.getReader();
      readerRef.current = reader;
      const decoder = new TextDecoder();
      let accumulated = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        accumulated += chunk;
        setDraftText(accumulated);
      }

      originalTextRef.current = accumulated;
      setState("ready");
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Streaming failed.");
      setState("error");
    }
  }, [emailId]);

  // ------------------------------------------------------------------
  // On mount: check for existing draft, otherwise stream
  // ------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        const res = await api.get<{ email: Email; draft: Draft | null }>(`/emails/${emailId}`);
        if (cancelled) return;

        const existing = res.draft;
        if (existing && existing.draft_text) {
          setDraft(existing);
          const text = existing.edited_text ?? existing.draft_text;
          setDraftText(text);
          originalTextRef.current = text;
          setState("ready");
        } else {
          // Draft record exists but is empty — stream it
          startStream();
        }
      } catch (err) {
        if (cancelled) return;
        // 404 → email not found (shouldn't happen), generate anyway
        if (err instanceof ApiError && err.status === 404) {
          startStream();
        } else {
          setError(err instanceof Error ? err.message : "Failed to load draft.");
          setState("error");
        }
      }
    }

    init();

    return () => {
      cancelled = true;
      // Cancel any in-flight stream reader
      readerRef.current?.cancel().catch(() => {});
    };
  }, [emailId, startStream]);

  // ------------------------------------------------------------------
  // send
  // ------------------------------------------------------------------
  const send = useCallback(
    async (editedText: string) => {
      setState("sending");
      try {
        await api.post(`/emails/${emailId}/send`, { edited_text: editedText });

        // If the user edited the generated text, fire silent feedback
        if (editedText.trim() !== originalTextRef.current.trim()) {
          api
            .post("/eval/feedback", {
              ai_call_id: draft?.id ?? emailId,
              feature: "draft",
              rating: 2,
              correction: editedText,
              notes: null,
            })
            .catch(() => {}); // fire-and-forget
        }

        setState("sent");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to send.");
        setState("error");
      }
    },
    [emailId, draft],
  );

  // ------------------------------------------------------------------
  // discard
  // ------------------------------------------------------------------
  const discard = useCallback(async () => {
    if (!draft) return;
    try {
      await api.del(`/emails/${emailId}/draft`);
      setDraft(null);
      setDraftText("");
      setState("ready"); // leave page logic to the component
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to discard draft.");
      setState("error");
    }
  }, [draft]);

  return { draft, draftText, state, error, send, discard };
}
