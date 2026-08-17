"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Loader2, SendHorizonal, Sparkles, X } from "lucide-react";
import type { AssistItem } from "@/lib/types";
import type { AssistAskOptions } from "@/hooks/useMeetingCapture";
import { AssistCard } from "./AssistCard";

interface AssistSidebarProps {
  items: AssistItem[];
  onDismiss: (id: string) => void;
  /**
   * Returns false when the question couldn't be sent or answered. The WebSocket
   * transport knows that synchronously (the socket is either open or it isn't);
   * the REST transport only knows once the response lands, so this may also be
   * a promise — either way the typed question survives a failure.
   */
  onAsk: (question: string, options?: AssistAskOptions) => boolean | Promise<boolean>;
  askPending: boolean;
  askError: string | null;
  onClose: () => void;
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
 * The live-assist panel: a quiet stream of context cards plus an ask box.
 * Rendered as the third grid column on lg screens and as a right-side overlay
 * drawer below that (the parent owns the positioning wrapper).
 */
export function AssistSidebar({
  items,
  onDismiss,
  onAsk,
  askPending,
  askError,
  onClose,
  interviewMode = false,
  standaloneMode = false,
  onBeforeAsk,
}: AssistSidebarProps) {
  const [question, setQuestion] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);
  // askPending only goes true once onAsk runs, which is after an await here —
  // this closes the window where two fast Enters would both get through.
  const sendingRef = useRef(false);

  // Keep the newest card in view as they arrive.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  async function send() {
    if (askPending || sendingRef.current || !question.trim()) return;
    sendingRef.current = true;
    try {
      try {
        await onBeforeAsk?.();
      } catch {
        // A failed notes flush is not a reason to refuse the question — it just
        // means the answer sees slightly older notes.
      }
      // Keep the typed question if the send failed (e.g. socket reconnecting,
      // or the request errored) so the user can retry instead of retyping it.
      if (await onAsk(question)) setQuestion("");
    } finally {
      sendingRef.current = false;
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
    <div className="flex h-full min-h-0 flex-col rounded-lg border border-white/[0.04] bg-[#0d1526]/60">
      <div className="flex items-center justify-between border-b border-white/[0.04] p-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-indigo-400" />
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Live assist
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close live assist"
          className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {items.length === 0 ? (
          <p className="pt-6 text-center text-xs leading-relaxed text-slate-600">
            {standaloneMode
              ? "Ask about previous meetings, something happening now, or any other question."
              : "Felix is listening quietly. When something relevant comes up — context, a fact, an open commitment — it appears here."}
          </p>
        ) : (
          items.map((item) => (
            <AssistCard
              key={item.id}
              item={item}
              onDismiss={onDismiss}
              onExpand={onAsk}
              askPending={askPending}
            />
          ))
        )}
      </div>

      <form onSubmit={submit} className="border-t border-white/[0.04] p-3">
        {askError && <p className="mb-2 text-xs text-red-400">{askError}</p>}
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
            className="min-w-0 flex-1 resize-none rounded-lg border border-slate-700/60 bg-slate-800/40 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={askPending || !question.trim()}
            aria-label="Send question"
            className="rounded-lg bg-indigo-600 p-2 text-white transition-colors hover:bg-indigo-500 disabled:opacity-40"
          >
            {askPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <SendHorizonal className="h-4 w-4" />
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
