"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  CheckSquare,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { useMeeting, useMeetings } from "@/hooks/useMeetings";
import { EnhancedNotes } from "@/components/meetings/EnhancedNotes";
import { STATUS_META, templateLabel } from "@/components/meetings/constants";
import type { ActionItem, Meeting } from "@/lib/types";

interface PageProps {
  params: { id: string };
}

function whenLabel(m: Meeting): string {
  const raw = m.started_at || m.date || m.created_at;
  return raw
    ? new Date(raw).toLocaleString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function MeetingDetailPage({ params }: PageProps) {
  const { id } = params;
  const router = useRouter();
  const { meeting, segments, summary, isLoading, error, resummarize } =
    useMeeting(id);
  const { deleteMeeting } = useMeetings();
  const [retrying, setRetrying] = useState(false);
  const [deleting, setDeleting] = useState(false);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
      </div>
    );
  }

  if (error || !meeting) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-500">
        <p>Meeting not found.</p>
        <Link href="/meetings" className="text-indigo-400 hover:underline">
          Back to meetings
        </Link>
      </div>
    );
  }

  const meta = STATUS_META[meeting.status] ?? STATUS_META.idle;

  async function handleRetry() {
    setRetrying(true);
    try {
      await resummarize();
    } finally {
      setRetrying(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete this meeting and its transcript? This can't be undone.")) {
      return;
    }
    setDeleting(true);
    try {
      await deleteMeeting(id);
      router.push("/meetings");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <Link
            href="/meetings"
            className="mt-0.5 rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
            aria-label="Back to meetings"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-slate-100">
                {meeting.title || "Untitled meeting"}
              </h1>
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${meta.className}`}
              >
                {meta.label}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {templateLabel(meeting.template)}
              {whenLabel(meeting) && <> · {whenLabel(meeting)}</>}
            </p>
          </div>
        </div>
        <button
          onClick={handleDelete}
          disabled={deleting}
          aria-label="Delete meeting"
          className="shrink-0 rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-700/50 hover:text-red-400 disabled:opacity-50"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {/* Processing / error states */}
      {meeting.status === "processing" && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
          <Loader2 className="h-4 w-4 animate-spin" />
          Summarizing the meeting — this page will update when it’s ready.
        </div>
      )}

      {meeting.status === "error" && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
          <span>Summarization failed. You can retry it.</span>
          <button
            onClick={handleRetry}
            disabled={retrying}
            className="flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${retrying ? "animate-spin" : ""}`} />
            Retry
          </button>
        </div>
      )}

      {/* Summary */}
      {summary && (
        <>
          {summary.tldr && (
            <Card title="TL;DR">
              <p className="text-sm text-slate-200">{summary.tldr}</p>
            </Card>
          )}

          {summary.enhanced_notes.length > 0 && (
            <Card title="Enhanced notes">
              <EnhancedNotes notes={summary.enhanced_notes} />
            </Card>
          )}

          {summary.decisions.length > 0 && (
            <Card title="Decisions">
              <ul className="space-y-1.5">
                {summary.decisions.map((d, i) => (
                  <li key={i} className="flex gap-2 text-sm text-slate-200">
                    <span className="text-slate-500">•</span>
                    {d.text}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {summary.action_items.length > 0 && (
            <Card title="Action items">
              <ul className="space-y-2">
                {summary.action_items.map((item, i) => (
                  <ActionItemRow key={i} item={item} />
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      {/* Transcript (collapsible) */}
      {segments.length > 0 && (
        <details className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-slate-400">
            Full transcript ({segments.length} segments)
          </summary>
          <div className="mt-3 space-y-2">
            {segments.map((s) => (
              <p key={s.id} className="text-sm">
                <span
                  className={
                    s.speaker === "me"
                      ? "font-medium text-indigo-300"
                      : "font-medium text-slate-300"
                  }
                >
                  {s.speaker === "me" ? "You" : "Them"}:
                </span>{" "}
                <span className="text-slate-300">{s.text}</span>
              </p>
            ))}
          </div>
        </details>
      )}

      {!summary && meeting.status === "done" && (
        <p className="text-sm text-slate-500">No summary was produced for this meeting.</p>
      )}
    </div>
  );
}

/** owner:'me' items become commitments — deep-link there; others just show the owner. */
function ActionItemRow({ item }: { item: ActionItem }) {
  const mine = (item.owner || "").trim().toLowerCase() === "me";
  return (
    <li className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 gap-2 text-sm text-slate-200">
        <CheckSquare className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
        <span className="min-w-0">
          {item.text}
          <span className="ml-2 text-xs text-slate-500">
            {mine ? "you" : item.owner}
            {item.due_hint ? ` · ${item.due_hint}` : ""}
          </span>
        </span>
      </div>
      {mine && (
        <Link
          href="/commitments"
          className="shrink-0 text-xs text-indigo-400 hover:underline"
        >
          View
        </Link>
      )}
    </li>
  );
}
