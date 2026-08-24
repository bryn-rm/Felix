"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Trash2 } from "lucide-react";

import { isMeetingCaptureSupported } from "@/lib/capture-support";
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

/**
 * Where a row links: the live surface while recording, detail otherwise.
 *
 * Which live surface depends on what the client can do, not on what it is. A
 * capture-capable browser gets the capture page (unchanged — this is the
 * laptop's normal path, including resuming after a refresh). A client that
 * cannot capture would land there on a disabled Start button and have to click
 * through, so it goes straight to the viewer, which is the only live surface it
 * can actually use. A manual session has no capture step for anyone.
 */
function hrefFor(m: Meeting, canCapture: boolean): string {
  if (m.status !== "recording") return `/meetings/${m.id}`;
  if (canCapture || m.source === "manual_notes") return `/meetings/live/${m.id}`;
  return `/meetings/live/${m.id}/viewer`;
}

/**
 * Capability probe, resolved after mount.
 *
 * Starts true so the server render and the first client render agree (no
 * hydration mismatch) and the laptop's link is right from the first paint; the
 * effect then corrects it on a client that cannot capture. Deliberately a
 * capability check rather than a user-agent test — a desktop browser without
 * tab-audio sharing is an observer too.
 */
function useCanCapture(): boolean {
  const [canCapture, setCanCapture] = useState(true);
  useEffect(() => setCanCapture(isMeetingCaptureSupported()), []);
  return canCapture;
}

export function MeetingList({
  meetings,
  onDelete,
}: {
  meetings: Meeting[];
  onDelete: (id: string) => Promise<void>;
}) {
  const canCapture = useCanCapture();
  return (
    <div className="space-y-3 pb-6">
      {meetings.map((m) => (
        <MeetingRow key={m.id} m={m} onDelete={onDelete} canCapture={canCapture} />
      ))}
    </div>
  );
}

function MeetingRow({
  m,
  onDelete,
  canCapture,
}: {
  m: Meeting;
  onDelete: (id: string) => Promise<void>;
  canCapture: boolean;
}) {
  const [deleting, setDeleting] = useState(false);
  const meta = STATUS_META[m.status] ?? STATUS_META.idle;
  const manualSession = m.source === "manual_notes";

  async function handleDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`Delete this ${manualSession ? "assistant session" : "meeting and its transcript"}? This can't be undone.`)) {
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
      href={hrefFor(m, canCapture)}
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
            {manualSession && m.status === "recording" ? "Open" : meta.label}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {manualSession ? "Manual assistant" : templateLabel(m.template)}
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
