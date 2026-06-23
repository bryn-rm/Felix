"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Building2,
  ExternalLink,
  Loader2,
  Mail,
  Sparkles,
} from "lucide-react";

import { api } from "@/lib/api";
import { useJob } from "@/hooks/useJobs";
import { JobTimeline } from "@/components/jobs/JobTimeline";
import { ALL_STATUSES } from "@/components/jobs/constants";
import type { JobStatus } from "@/lib/types";

interface PageProps {
  params: { id: string };
}

export default function JobDetailPage({ params }: PageProps) {
  const { id } = params;
  const { job, events, isLoading, error, updateJob, addNote, draftFollowUp } =
    useJob(id);

  const [note, setNote] = useState("");
  const [savingNote, setSavingNote] = useState(false);

  const [draft, setDraft] = useState<string | null>(null);
  const [draftEmailId, setDraftEmailId] = useState<string | null>(null);
  const [draftingFollowUp, setDraftingFollowUp] = useState(false);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-500">
        <p>Job not found.</p>
        <Link href="/jobs" className="text-indigo-400 hover:underline">
          Back to board
        </Link>
      </div>
    );
  }

  async function handleDraftFollowUp() {
    setDraftingFollowUp(true);
    setDraftNote(null);
    setDraft(null);
    try {
      const res = await draftFollowUp();
      if (res.draft) {
        setDraft(res.draft.draft_text);
        setDraftEmailId(res.email_id ?? null);
      } else {
        setDraftNote(
          res.reason === "no_threaded_email"
            ? "No email thread to reply to yet — reach out manually for now."
            : "Couldn't generate a draft.",
        );
      }
    } catch (e) {
      setDraftNote(e instanceof Error ? e.message : "Failed to draft follow-up.");
    } finally {
      setDraftingFollowUp(false);
    }
  }

  async function handleSend() {
    if (!draftEmailId || !draft) return;
    setSending(true);
    try {
      await api.post(`/emails/${draftEmailId}/send`, { edited_text: draft });
      setDraft(null);
      setDraftEmailId(null);
      setDraftNote("Follow-up sent.");
    } catch (e) {
      setDraftNote(e instanceof Error ? e.message : "Failed to send.");
    } finally {
      setSending(false);
    }
  }

  async function handleAddNote() {
    if (!note.trim()) return;
    setSavingNote(true);
    try {
      await addNote(note.trim());
      setNote("");
    } finally {
      setSavingNote(false);
    }
  }

  const input =
    "rounded-md border border-slate-600 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none";

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-5 overflow-y-auto p-6">
      <Link
        href="/jobs"
        className="flex w-fit items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
      >
        <ArrowLeft className="h-4 w-4" />
        Board
      </Link>

      {/* Header */}
      <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
        <h1 className="text-lg font-semibold text-slate-100">{job.role_title}</h1>
        <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-400">
          <Building2 className="h-4 w-4" />
          {job.company}
          {job.location ? ` · ${job.location}` : ""}
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="text-xs text-slate-500">Stage</label>
          <select
            className={input}
            value={job.status}
            onChange={(e) => updateJob({ status: e.target.value as JobStatus })}
          >
            {ALL_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>

          {job.job_url && (
            <a
              href={job.job_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-indigo-400 hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              Job posting
            </a>
          )}
        </div>

        {(job.contact_name || job.contact_email) && (
          <p className="mt-3 flex items-center gap-1.5 text-sm text-slate-400">
            <Mail className="h-4 w-4" />
            {job.contact_name}
            {job.contact_email ? ` · ${job.contact_email}` : ""}
          </p>
        )}

        {job.next_action && (
          <p className="mt-2 text-sm text-amber-300/90">
            Next: {job.next_action}
            {job.next_action_at
              ? ` (${new Date(job.next_action_at).toLocaleDateString()})`
              : ""}
          </p>
        )}

        <div className="mt-4">
          <button
            onClick={handleDraftFollowUp}
            disabled={draftingFollowUp}
            className="flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            {draftingFollowUp ? "Drafting…" : "Draft follow-up"}
          </button>
        </div>

        {draftNote && <p className="mt-2 text-sm text-slate-400">{draftNote}</p>}

        {draft !== null && (
          <div className="mt-3 rounded-md border border-slate-700 bg-slate-900/60 p-3">
            <p className="mb-2 text-xs uppercase tracking-wider text-slate-500">
              Review &amp; send
            </p>
            <textarea
              className={`${input} min-h-[160px] w-full`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <div className="mt-2 flex justify-end gap-2">
              <button
                onClick={() => setDraft(null)}
                className="rounded-md bg-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-600"
              >
                Discard
              </button>
              <button
                onClick={handleSend}
                disabled={sending || !draftEmailId}
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {sending ? "Sending…" : "Send"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Notes */}
      {job.notes && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
          <p className="mb-1 text-xs uppercase tracking-wider text-slate-500">Notes</p>
          <p className="whitespace-pre-wrap text-sm text-slate-300">{job.notes}</p>
        </div>
      )}

      {/* Timeline */}
      <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4">
        <p className="mb-3 text-xs uppercase tracking-wider text-slate-500">Timeline</p>
        <JobTimeline events={events} />

        <div className="mt-4 flex gap-2">
          <input
            className={`${input} flex-1`}
            placeholder="Add a note…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAddNote();
            }}
          />
          <button
            onClick={handleAddNote}
            disabled={savingNote || !note.trim()}
            className="rounded-md bg-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 disabled:opacity-50"
          >
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
