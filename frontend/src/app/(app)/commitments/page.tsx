"use client";

import { useState } from "react";
import { Check, Clock, X } from "lucide-react";

import { useCommitments, type CommitmentDirection } from "@/hooks/useCommitments";
import type { Commitment } from "@/lib/types";

const TABS: { label: string; value: CommitmentDirection }[] = [
  { label: "I owe", value: "owed_by_user" },
  { label: "They owe", value: "owed_to_user" },
  { label: "All", value: "all" },
];

function deadlineLabel(c: Commitment): { label: string; tone: "overdue" | "soon" | "later" | "none" } {
  if (!c.deadline) return { label: "no deadline", tone: "none" };
  const due = new Date(c.deadline);
  const now = new Date();
  const ms = due.getTime() - now.getTime();
  const hours = ms / (1000 * 60 * 60);
  const days = Math.round(hours / 24);
  if (ms < 0) return { label: `overdue · ${due.toLocaleDateString()}`, tone: "overdue" };
  if (hours < 48) return { label: `due ${due.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}`, tone: "soon" };
  return { label: `due in ${days} day${days === 1 ? "" : "s"}`, tone: "later" };
}

function counterpartyLabel(c: Commitment): string {
  return c.counterparty_name || (c.counterparty_email || "").split("@")[0] || "unknown";
}

function CommitmentCard({
  c,
  onResolve,
}: {
  c: Commitment;
  onResolve: (id: string, status: "done" | "dropped") => Promise<void>;
}) {
  const [busy, setBusy] = useState<null | "done" | "dropped">(null);
  const dl = deadlineLabel(c);
  const verb = c.direction === "owed_by_user" ? "you owe" : "they owe you";
  const toneClass =
    dl.tone === "overdue"
      ? "text-red-400"
      : dl.tone === "soon"
        ? "text-amber-400"
        : "text-slate-400";

  async function handle(status: "done" | "dropped") {
    setBusy(status);
    try {
      await onResolve(c.id, status);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs uppercase tracking-wider text-slate-500">
            {verb} <span className="text-slate-300">{counterpartyLabel(c)}</span>
          </p>
          <p className="mt-1 text-sm text-slate-100">{c.text}</p>
          {c.source_quote && (
            <p className="mt-2 line-clamp-2 text-xs italic text-slate-500">
              “{c.source_quote}”
            </p>
          )}
          <div className="mt-2 flex items-center gap-3">
            <span className={`flex items-center gap-1 text-xs ${toneClass}`}>
              <Clock className="h-3 w-3" />
              {dl.label}
            </span>
            <span className="text-xs text-slate-600">
              confidence {Math.round((c.confidence ?? 0) * 100)}%
            </span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col gap-2">
          <button
            onClick={() => handle("done")}
            disabled={busy !== null}
            className="flex items-center gap-1 rounded-md bg-emerald-600/80 px-2 py-1 text-xs text-white transition-colors hover:bg-emerald-600 disabled:opacity-50"
            aria-label="Mark done"
          >
            <Check className="h-3 w-3" />
            Done
          </button>
          <button
            onClick={() => handle("dropped")}
            disabled={busy !== null}
            className="flex items-center gap-1 rounded-md bg-slate-700 px-2 py-1 text-xs text-slate-300 transition-colors hover:bg-slate-600 disabled:opacity-50"
            aria-label="Drop"
          >
            <X className="h-3 w-3" />
            Drop
          </button>
        </div>
      </div>
    </div>
  );
}

export default function CommitmentsPage() {
  const [tab, setTab] = useState<CommitmentDirection>("owed_by_user");
  const { commitments, isLoading, error, resolve } = useCommitments(tab, "open");

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Commitments</h1>
        <p className="mt-1 text-sm text-slate-500">
          Promises captured from your email — both directions. Felix surfaces these in
          your morning briefing as deadlines approach.
        </p>
      </div>

      <div className="flex w-fit gap-1 rounded-lg border border-slate-700 bg-slate-800/40 p-1">
        {TABS.map(({ label, value }) => {
          const active = tab === value;
          return (
            <button
              key={value}
              onClick={() => setTab(value)}
              className={[
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                active
                  ? "bg-slate-700 text-slate-100"
                  : "text-slate-400 hover:text-slate-200",
              ].join(" ")}
            >
              {label}
            </button>
          );
        })}
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-20 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
              style={{ animationDelay: `${i * 80}ms` }}
            />
          ))}
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400">
          Failed to load commitments: {error.message}
        </p>
      )}

      {!isLoading && !error && commitments.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <p className="text-base font-medium text-slate-300">All clear</p>
          <p className="text-sm text-slate-500">
            {tab === "owed_by_user"
              ? "Nothing you've promised is outstanding."
              : tab === "owed_to_user"
                ? "Nobody owes you anything that Felix can see."
                : "No open commitments tracked right now."}
          </p>
        </div>
      )}

      {!isLoading && !error && commitments.length > 0 && (
        <div className="space-y-3 pb-6">
          {commitments.map((c) => (
            <CommitmentCard key={c.id} c={c} onResolve={resolve} />
          ))}
        </div>
      )}
    </div>
  );
}
