"use client";

import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  FileText,
  GitCommitHorizontal,
  Send,
} from "lucide-react";
import type { JobEvent, JobEventType } from "@/lib/types";

const ICONS: Record<JobEventType, React.ElementType> = {
  applied: CheckCircle2,
  email_in: ArrowDownLeft,
  email_out: ArrowUpRight,
  interview_scheduled: CalendarClock,
  status_change: GitCommitHorizontal,
  note: FileText,
  follow_up_sent: Send,
};

function eventLabel(e: JobEvent): string {
  if (e.title) return e.title;
  return e.event_type.replace(/_/g, " ");
}

export function JobTimeline({ events }: { events: JobEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-slate-500">No activity yet.</p>;
  }

  return (
    <ol className="space-y-4">
      {events.map((e) => {
        const Icon = ICONS[e.event_type] ?? FileText;
        return (
          <li key={e.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-700 bg-slate-800 text-slate-300">
                <Icon className="h-3.5 w-3.5" />
              </span>
              <span className="mt-1 w-px flex-1 bg-slate-700/50" />
            </div>
            <div className="min-w-0 flex-1 pb-1">
              <p className="text-sm text-slate-100">{eventLabel(e)}</p>
              {e.detail && e.detail !== e.title && (
                <p className="mt-0.5 line-clamp-3 text-xs text-slate-500">{e.detail}</p>
              )}
              <p className="mt-0.5 text-[11px] text-slate-600">
                {new Date(e.occurred_at).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
