"use client";

import { useRouter } from "next/navigation";
import { Bell, Building2, User } from "lucide-react";
import type { JobApplication } from "@/lib/types";

function dueLabel(job: JobApplication): string | null {
  if (!job.next_action_at) return null;
  const due = new Date(job.next_action_at);
  const now = new Date();
  if (due.getTime() <= now.getTime()) {
    return job.next_action || "Action due";
  }
  return null;
}

export function JobCard({
  job,
  onDragStart,
  onDragEnd,
  dragging,
}: {
  job: JobApplication;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  dragging: boolean;
}) {
  const router = useRouter();
  const due = dueLabel(job);

  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", job.id);
        onDragStart(job.id);
      }}
      onDragEnd={onDragEnd}
      onClick={() => router.push(`/jobs/${job.id}`)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter") router.push(`/jobs/${job.id}`);
      }}
      className={[
        "cursor-pointer rounded-lg border border-slate-700/50 bg-slate-800/40 p-3",
        "transition-colors hover:border-slate-600 hover:bg-slate-800/70",
        dragging ? "opacity-40" : "",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-sm font-medium text-slate-100">
          {job.role_title}
        </p>
        {due && (
          <span
            className="flex shrink-0 items-center gap-1 rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300"
            title={due}
          >
            <Bell className="h-3 w-3" />
            Due
          </span>
        )}
      </div>
      <p className="mt-1 flex items-center gap-1 truncate text-xs text-slate-400">
        <Building2 className="h-3 w-3 shrink-0" />
        {job.company}
        {job.location ? ` · ${job.location}` : ""}
      </p>
      {(job.contact_name || job.contact_email) && (
        <p className="mt-1 flex items-center gap-1 truncate text-xs text-slate-500">
          <User className="h-3 w-3 shrink-0" />
          {job.contact_name || job.contact_email}
        </p>
      )}
      {due && <p className="mt-2 truncate text-xs text-amber-300/80">{due}</p>}
    </div>
  );
}
