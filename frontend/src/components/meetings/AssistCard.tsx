"use client";

import { X } from "lucide-react";
import type { AssistItem, AssistKind } from "@/lib/types";

const KIND_STYLE: Record<AssistKind, { label: string; className: string }> = {
  context:       { label: "Context",       className: "bg-sky-500/15 text-sky-300" },
  answer:        { label: "Answer",        className: "bg-indigo-500/15 text-indigo-300" },
  fact:          { label: "Fact",          className: "bg-emerald-500/15 text-emerald-300" },
  contradiction: { label: "Heads up",      className: "bg-amber-500/15 text-amber-300" },
  follow_up:     { label: "Follow-up",     className: "bg-fuchsia-500/15 text-fuchsia-300" },
};

function formatTs(ts: number | null): string | null {
  if (ts === null || ts === undefined || !Number.isFinite(ts)) return null;
  const total = Math.max(0, Math.floor(ts));
  const mm = Math.floor(total / 60);
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export function AssistCard({
  item,
  onDismiss,
}: {
  item: AssistItem;
  onDismiss: (id: string) => void;
}) {
  const kind = KIND_STYLE[item.kind] ?? KIND_STYLE.context;
  const ts = formatTs(item.transcript_ts);
  return (
    <div className="rounded-lg border border-white/[0.04] bg-[#0d1526] p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${kind.className}`}
          >
            {kind.label}
          </span>
          {ts && <span className="text-[10px] text-slate-500">{ts}</span>}
        </div>
        <button
          onClick={() => onDismiss(item.id)}
          aria-label="Dismiss suggestion"
          className="rounded p-0.5 text-slate-500 hover:bg-slate-700/50 hover:text-slate-300"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {item.question && (
        <p className="mt-2 text-xs italic text-slate-500">“{item.question}”</p>
      )}
      <p className="mt-1.5 text-sm font-medium text-slate-200">{item.title}</p>
      <p className="mt-1 text-sm leading-snug text-slate-400">{item.body}</p>
    </div>
  );
}
