"use client";

import { useEffect, useRef } from "react";
import { Sparkles, X } from "lucide-react";
import type { AssistAskFn, AssistItem } from "@/lib/types";
import { AssistCard } from "./AssistCard";
import { AssistComposer } from "./AssistComposer";

interface AssistSidebarProps {
  items: AssistItem[];
  onDismiss: (id: string) => void;
  /**
   * Returns false when the question couldn't be sent or answered. The WebSocket
   * transport knows that synchronously (the socket is either open or it isn't);
   * the REST transport only knows once the response lands, so this may also be
   * a promise — either way the typed question survives a failure.
   */
  onAsk: AssistAskFn;
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
  const listRef = useRef<HTMLDivElement | null>(null);

  // Keep the newest card in view as they arrive.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length]);

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

      <AssistComposer
        onAsk={onAsk}
        askPending={askPending}
        askError={askError}
        interviewMode={interviewMode}
        standaloneMode={standaloneMode}
        onBeforeAsk={onBeforeAsk}
      />
    </div>
  );
}
