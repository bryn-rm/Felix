"use client";

import { useState } from "react";
import { Check, Sparkles, X } from "lucide-react";
import type { JobSuggestion } from "@/lib/types";

export function SuggestionBanner({
  suggestions,
  onResolve,
}: {
  suggestions: JobSuggestion[];
  onResolve: (id: string, accept: boolean) => Promise<void>;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-600/10 p-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-indigo-300">
        <Sparkles className="h-3.5 w-3.5" />
        Suggested jobs ({suggestions.length})
      </p>
      <div className="space-y-2">
        {suggestions.map((s) => (
          <SuggestionRow key={s.id} s={s} onResolve={onResolve} />
        ))}
      </div>
    </div>
  );
}

function SuggestionRow({
  s,
  onResolve,
}: {
  s: JobSuggestion;
  onResolve: (id: string, accept: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  async function handle(accept: boolean) {
    setBusy(true);
    try {
      await onResolve(s.id, accept);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-3 rounded-md bg-slate-800/50 px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-slate-100">
          {s.role_title || "Unknown role"}
          {s.company ? ` · ${s.company}` : ""}
        </p>
        {s.summary && (
          <p className="truncate text-xs text-slate-500">{s.summary}</p>
        )}
        <p className="text-[10px] text-slate-600">
          {s.proposed_status ? `${s.proposed_status} · ` : ""}
          {Math.round((s.confidence ?? 0) * 100)}% confident
        </p>
      </div>
      <div className="flex shrink-0 gap-1.5">
        <button
          onClick={() => handle(true)}
          disabled={busy}
          className="flex items-center gap-1 rounded-md bg-emerald-600/80 px-2 py-1 text-xs text-white transition-colors hover:bg-emerald-600 disabled:opacity-50"
        >
          <Check className="h-3 w-3" />
          Add
        </button>
        <button
          onClick={() => handle(false)}
          disabled={busy}
          className="flex items-center gap-1 rounded-md bg-slate-700 px-2 py-1 text-xs text-slate-300 transition-colors hover:bg-slate-600 disabled:opacity-50"
        >
          <X className="h-3 w-3" />
          Dismiss
        </button>
      </div>
    </div>
  );
}
