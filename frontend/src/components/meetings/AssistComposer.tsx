"use client";

import { FormEvent, KeyboardEvent, useRef, useState } from "react";
import { Loader2, SendHorizonal } from "lucide-react";
import type { AssistAskFn } from "@/lib/types";

interface AssistComposerProps {
  onAsk: AssistAskFn;
  askPending: boolean;
  askError: string | null;
  interviewMode?: boolean;
  standaloneMode?: boolean;
  /**
   * Run just before the question goes out — the live page uses it to flush the
   * debounced notes autosave, so an ask about a line typed a second ago isn't
   * answered against notes the server hasn't seen yet.
   */
  onBeforeAsk?: () => void | Promise<void>;
}

/**
 * The ask box: textarea, send button, pending/error state.
 *
 * Extracted from AssistSidebar so the phone viewer can offer the same ask
 * without inheriting the sidebar's capture-side chrome (close button, card
 * stream, dismissal). The behaviour that matters on a phone — a failed send
 * keeps what you typed, Enter submits — is defined once, here.
 */
export function AssistComposer({
  onAsk,
  askPending,
  askError,
  interviewMode = false,
  standaloneMode = false,
  onBeforeAsk,
}: AssistComposerProps) {
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // askPending only goes true once onAsk runs, which is after an await here —
  // this closes the window where two fast Enters would both get through.
  const sendingRef = useRef(false);
  const busy = submitting || askPending;

  async function send() {
    if (busy || sendingRef.current || !question.trim()) return;
    const submittedQuestion = question;
    sendingRef.current = true;
    setSubmitting(true);
    // Give immediate feedback. If the transport ultimately fails, restore the
    // question below so retry is still one keypress and no text is lost.
    setQuestion("");
    try {
      try {
        await onBeforeAsk?.();
      } catch {
        // A failed notes flush is not a reason to refuse the question — it just
        // means the answer sees slightly older notes.
      }
      // Keep the typed question if the send failed (e.g. socket reconnecting,
      // the request errored, or another device's ask holds the meeting's slot)
      // so the user can retry instead of retyping it.
      if (!(await onAsk(submittedQuestion))) {
        // Do not overwrite a next question the user started drafting while the
        // previous one was in flight.
        setQuestion((current) => current || submittedQuestion);
      }
    } finally {
      sendingRef.current = false;
      setSubmitting(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void send();
  }

  // A textarea has no implicit form submit, and mid-meeting the user reaches
  // for Enter, not the send button. Shift+Enter keeps the newline so a pasted
  // multi-line interview prompt is still editable.
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    void send();
  }

  return (
    <form onSubmit={submit} className="border-t border-white/[0.04] p-3">
      {askError && !submitting && (
        <p className="mb-2 text-xs text-red-400">{askError}</p>
      )}
      {busy && (
        <p role="status" className="mb-2 flex items-center gap-1.5 text-xs text-indigo-300">
          <Loader2 className="h-3 w-3 animate-spin" />
          Asking Felix…
        </p>
      )}
      <div className="flex items-center gap-2">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={6000}
          rows={interviewMode ? 3 : 2}
          placeholder={
            interviewMode
              ? "Ask Felix or paste an interview prompt…"
              : standaloneMode
                ? "Ask about a previous meeting or anything else…"
                : "Ask Felix…"
          }
          aria-label="Ask Felix a question"
          aria-busy={busy}
          className="min-w-0 flex-1 resize-none rounded-lg border border-slate-700/60 bg-slate-800/40 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          aria-label="Send question"
          className="rounded-lg bg-indigo-600 p-2 text-white transition-colors hover:bg-indigo-500 disabled:opacity-40"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <SendHorizonal className="h-4 w-4" />
          )}
        </button>
      </div>
    </form>
  );
}
