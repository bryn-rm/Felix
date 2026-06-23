"use client";

import { useState } from "react";
import { Plus } from "lucide-react";

import { useJobs } from "@/hooks/useJobs";
import { JobBoard } from "@/components/jobs/JobBoard";
import { AddJobModal } from "@/components/jobs/AddJobModal";
import { SuggestionBanner } from "@/components/jobs/SuggestionBanner";

export default function JobsPage() {
  const { board, isLoading, error, suggestions, addJob, moveJob, resolveSuggestion } =
    useJobs();
  const [showAdd, setShowAdd] = useState(false);

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Jobs</h1>
          <p className="mt-1 text-sm text-slate-500">
            Your application pipeline. Felix tracks progress from your email — drag a
            card to move it, or add one manually.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          <Plus className="h-4 w-4" />
          Add job
        </button>
      </div>

      <SuggestionBanner suggestions={suggestions} onResolve={resolveSuggestion} />

      {isLoading && (
        <div className="flex gap-3">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="h-64 w-64 shrink-0 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
              style={{ animationDelay: `${i * 80}ms` }}
            />
          ))}
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400">Failed to load board: {error.message}</p>
      )}

      {!isLoading && !error && board && board.total === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <p className="text-base font-medium text-slate-300">No jobs tracked yet</p>
          <p className="text-sm text-slate-500">
            Add one manually, or let Felix detect applications from your inbox.
          </p>
        </div>
      )}

      {!isLoading && !error && board && board.total > 0 && (
        <div className="min-h-0 flex-1">
          <JobBoard board={board} onMove={moveJob} />
        </div>
      )}

      {showAdd && (
        <AddJobModal
          onClose={() => setShowAdd(false)}
          onSubmit={async (values) => {
            await addJob({
              company: values.company.trim(),
              role_title: values.role_title.trim(),
              location: values.location.trim() || null,
              job_url: values.job_url.trim() || null,
              status: values.status,
              contact_name: values.contact_name.trim() || null,
              contact_email: values.contact_email.trim() || null,
              compensation: values.compensation.trim() || null,
              notes: values.notes.trim() || null,
            });
          }}
        />
      )}
    </div>
  );
}
