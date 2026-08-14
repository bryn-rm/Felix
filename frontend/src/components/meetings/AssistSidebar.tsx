"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Loader2, SendHorizonal, Sparkles, X } from "lucide-react";
import type { AssistItem } from "@/lib/types";
import { AssistCard } from "./AssistCard";

interface AssistSidebarProps {
  items: AssistItem[];
  onDismiss: (id: string) => void;
  /** Returns false when the question couldn't be sent (e.g. reconnecting). */
  onAsk: (question: string) => boolean;
  askPending: boolean;
  askError: string | null;
  onClose: () => void;
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
}: AssistSidebarProps) {
  const [question, setQuestion] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  // Keep the newest card in view as they arrive.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (askPending || !question.trim()) return;
    // Keep the typed question if the send failed (e.g. socket reconnecting) so
    // the user can retry instead of retyping it.
    if (onAsk(question)) setQuestion("");
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
            Felix is listening quietly. When something relevant comes up —
            context, a fact, an open commitment — it appears here.
          </p>
        ) : (
          items.map((item) => (
            <AssistCard key={item.id} item={item} onDismiss={onDismiss} />
          ))
        )}
      </div>

      <form onSubmit={submit} className="border-t border-white/[0.04] p-3">
        {askError && <p className="mb-2 text-xs text-red-400">{askError}</p>}
        <div className="flex items-center gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={1000}
            placeholder="Ask Felix…"
            aria-label="Ask Felix a question"
            className="min-w-0 flex-1 rounded-lg border border-slate-700/60 bg-slate-800/40 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none"
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
