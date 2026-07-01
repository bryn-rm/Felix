"use client";

import { useState } from "react";
import Link from "next/link";
import { Trash2 } from "lucide-react";

import type { Meeting } from "@/lib/types";
import { STATUS_META, templateLabel } from "@/components/meetings/constants";

function whenLabel(m: Meeting): string {
  const raw = m.started_at || m.date || m.created_at;
  if (!raw) return "";
  return new Date(raw).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Where a row links: live page while recording, detail otherwise. */
function hrefFor(m: Meeting): string {
  return m.status === "recording" ? `/meetings/live/${m.id}` : `/meetings/${m.id}`;
}

export function MeetingList({
  meetings,
  onDelete,
}: {
  meetings: Meeting[];
  onDelete: (id: string) => Promise<void>;
}) {
  return (
    <div className="space-y-3 pb-6">
      {meetings.map((m) => (
        <MeetingRow key={m.id} m={m} onDelete={onDelete} />
      ))}
    </div>
  );
}

function MeetingRow({
  m,
  onDelete,
}: {
  m: Meeting;
  onDelete: (id: string) => Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);
  const meta = STATUS_META[m.status] ?? STATUS_META.idle;

  async function handleDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Delete this meeting and its transcript? This can't be undone.")) {
      return;
    }
    setDeleting(true);
    try {
      await onDelete(m.id);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Link
      href={hrefFor(m)}
      className="flex items-center justify-between gap-3 rounded-lg border border-slate-700/50 bg-slate-800/40 p-4 transition-colors hover:border-slate-600"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium text-slate-100">
            {m.title || "Untitled meeting"}
          </p>
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${meta.className}`}
          >
            {meta.label}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {templateLabel(m.template)}
          {whenLabel(m) && <> · {whenLabel(m)}</>}
        </p>
      </div>
      <button
        onClick={handleDelete}
        disabled={deleting}
        aria-label="Delete meeting"
        className="shrink-0 rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-700/50 hover:text-red-400 disabled:opacity-50"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </Link>
  );
}
