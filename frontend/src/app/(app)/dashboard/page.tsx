"use client";

import { useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  Clock,
  CalendarDays,
  Inbox,
  AlertCircle,
  Play,
  BookOpen,
} from "lucide-react";
import { api } from "@/lib/api";
import { isOverdue } from "@/lib/follow-ups";
import { useEmails } from "@/hooks/useEmails";
import type { CalendarEvent, Briefing, FollowUp } from "@/lib/types";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60_000);
  const hrs = Math.floor(diff / 3_600_000);
  const days = Math.floor(diff / 86_400_000);
  if (mins < 60) return `${mins}m ago`;
  if (hrs < 24) return `${hrs}h ago`;
  if (days < 7) return d.toLocaleDateString("en", { weekday: "short" });
  return d.toLocaleDateString("en", { month: "short", day: "numeric" });
}

function formatEventTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en", {
    hour: "numeric",
    minute: "2-digit",
  });
}

const SKEL_W: Record<string, string> = {
  "1/4": "w-1/4",
  "1/3": "w-1/3",
  "1/2": "w-1/2",
  "2/3": "w-2/3",
  "3/4": "w-3/4",
  full: "w-full",
};

function SkeletonLine({ w = "full" }: { w?: string }) {
  return (
    <div
      className={`h-3 animate-pulse rounded bg-slate-700 ${SKEL_W[w] ?? "w-full"}`}
    />
  );
}

const URGENCY_CLASSES: Record<string, string> = {
  critical: "bg-red-500/20 text-red-400 ring-1 ring-red-500/30",
  high: "bg-orange-500/20 text-orange-400 ring-1 ring-orange-500/30",
  medium: "bg-yellow-500/20 text-yellow-400 ring-1 ring-yellow-500/30",
  low: "bg-slate-500/20 text-slate-400 ring-1 ring-slate-500/30",
};

// ---------------------------------------------------------------------------
// Widget shell
// ---------------------------------------------------------------------------

function Widget({
  title,
  icon: Icon,
  children,
  className = "",
}: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col gap-3 rounded-xl border border-slate-700/50 bg-slate-800/50 p-5 ${className}`}
    >
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-indigo-400" />
        <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Morning Briefing
// ---------------------------------------------------------------------------

function MorningBriefingCard() {
  const { data: briefingData, isLoading } = useSWR<{ briefing: Briefing | null }>(
    "/briefing/today",
    (url: string) => api.get<{ briefing: Briefing | null }>(url),
    { refreshInterval: 5 * 60 * 1000 },
  );
  const briefing = briefingData?.briefing;
  const audioRef = useRef<HTMLAudioElement | null>(null);

  if (isLoading) {
    return (
      <div className="rounded-xl border border-slate-700/50 bg-slate-800/50 p-5 space-y-2">
        <SkeletonLine w="1/4" />
        <SkeletonLine w="3/4" />
        <SkeletonLine w="1/2" />
      </div>
    );
  }

  if (!briefing) return null;

  const preview = briefing.text
    .split(/(?<=\.)\s+/)
    .slice(0, 2)
    .join(" ");

  function toggleAudio() {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) {
      el.play();
    } else {
      el.pause();
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-indigo-500/30 bg-indigo-600/10 p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2">
          <BookOpen className="h-4 w-4 text-indigo-400" />
          <h2 className="text-sm font-semibold text-slate-200">
            Morning Briefing
          </h2>
          <span className="text-xs text-slate-500">
            {formatTime(briefing.generated_at)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {briefing.audio_url && (
            <>
              <audio ref={audioRef} src={briefing.audio_url} preload="none" />
              <button
                onClick={toggleAudio}
                className="flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-500"
              >
                <Play className="h-3.5 w-3.5" />
                Play
              </button>
            </>
          )}
          <Link
            href="/briefing"
            className="text-xs font-medium text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Read full →
          </Link>
        </div>
      </div>
      <p className="text-sm leading-relaxed text-slate-300">{preview}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Priority Inbox widget
// ---------------------------------------------------------------------------

function PriorityInboxWidget() {
  const router = useRouter();
  const { emails, isLoading, error } = useEmails({
    category: "action_required",
    limit: 4,
  });

  return (
    <Widget title="Priority Inbox" icon={Inbox}>
      {isLoading && (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="space-y-1.5">
              <SkeletonLine w="1/2" />
              <SkeletonLine w="3/4" />
            </div>
          ))}
        </div>
      )}

      {error && (
        <p className="text-xs text-slate-500">Could not load emails.</p>
      )}

      {!isLoading && !error && emails.length === 0 && (
        <p className="text-xs text-slate-500">
          All clear — no action required.
        </p>
      )}

      {!isLoading && !error && emails.length > 0 && (
        <div className="space-y-2">
          {emails.slice(0, 4).map((email) => (
            <button
              key={email.id}
              onClick={() => router.push(`/inbox/${email.id}`)}
              className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-slate-700/50"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-semibold text-slate-200">
                  {email.from_name ?? email.from_email}
                </p>
                <p className="truncate text-xs text-slate-400">
                  {email.subject ?? "(no subject)"}
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                {email.urgency && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                      URGENCY_CLASSES[email.urgency] ?? URGENCY_CLASSES.low
                    }`}
                  >
                    {email.urgency}
                  </span>
                )}
                <span className="text-xs text-slate-500">
                  {formatTime(email.received_at)}
                </span>
              </div>
            </button>
          ))}
          <Link
            href="/inbox?tab=action_required"
            className="block pt-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            View all →
          </Link>
        </div>
      )}
    </Widget>
  );
}

// ---------------------------------------------------------------------------
// Today's Schedule widget
// ---------------------------------------------------------------------------

function TodaysScheduleWidget() {
  const { data: calendarData, isLoading, error } = useSWR<{ events: CalendarEvent[] }>(
    "calendar-today",
    () => api.get<{ events: CalendarEvent[] }>("/calendar/today"),
    { refreshInterval: 5 * 60 * 1000 },
  );

  // Filter to upcoming/in-progress (show next 3)
  const now = new Date();
  const upcoming = (calendarData?.events ?? [])
    .filter((e) => new Date(e.end) > now)
    .slice(0, 3);

  return (
    <Widget title="Today's Schedule" icon={CalendarDays}>
      {isLoading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="space-y-1.5">
              <SkeletonLine w="1/3" />
              <SkeletonLine w="2/3" />
            </div>
          ))}
        </div>
      )}

      {error && (
        <p className="text-xs text-slate-500">Could not load calendar.</p>
      )}

      {!isLoading && !error && upcoming.length === 0 && (
        <p className="text-xs text-slate-500">No more meetings today.</p>
      )}

      {!isLoading && !error && upcoming.length > 0 && (
        <div className="space-y-2">
          {upcoming.map((event) => (
            <div
              key={event.id}
              className="flex items-start gap-3 rounded-md px-2 py-1.5"
            >
              <div className="mt-0.5 shrink-0 text-right">
                <p className="text-xs font-medium text-indigo-400">
                  {formatEventTime(event.start)}
                </p>
                <p className="text-xs text-slate-500">
                  {formatEventTime(event.end)}
                </p>
              </div>
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-slate-200">
                  {event.title}
                </p>
                {event.location && (
                  <p className="truncate text-xs text-slate-500">
                    {event.location}
                  </p>
                )}
              </div>
            </div>
          ))}
          <Link
            href="/calendar"
            className="block pt-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Full calendar →
          </Link>
        </div>
      )}
    </Widget>
  );
}

// ---------------------------------------------------------------------------
// Waiting On widget
// ---------------------------------------------------------------------------

function WaitingOnWidget() {
  const router = useRouter();
  const { emails, total, isLoading, error } = useEmails({
    category: "waiting_on",
    limit: 3,
  });

  return (
    <Widget title="Waiting On" icon={Clock}>
      {isLoading && (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => (
            <SkeletonLine key={i} w="3/4" />
          ))}
        </div>
      )}

      {error && (
        <p className="text-xs text-slate-500">Could not load.</p>
      )}

      {!isLoading && !error && total === 0 && (
        <p className="text-xs text-slate-500">
          No emails waiting on a reply.
        </p>
      )}

      {!isLoading && !error && total > 0 && (
        <>
          <p className="text-2xl font-bold text-slate-100">
            {total}
            <span className="ml-1.5 text-sm font-normal text-slate-400">
              thread{total !== 1 ? "s" : ""}
            </span>
          </p>
          <div className="space-y-1.5">
            {emails.slice(0, 3).map((email) => (
              <button
                key={email.id}
                onClick={() =>
                  router.push(`/inbox/${email.id}`)
                }
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left transition-colors hover:bg-slate-700/50"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500" />
                <span className="truncate text-xs text-slate-300">
                  {email.from_name ?? email.from_email}
                  {email.subject && ` — ${email.subject}`}
                </span>
              </button>
            ))}
          </div>
          <button
            onClick={() => router.push("/inbox?tab=waiting_on")}
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            View all →
          </button>
        </>
      )}
    </Widget>
  );
}

// ---------------------------------------------------------------------------
// Follow-up Alerts widget
// ---------------------------------------------------------------------------

interface FollowUpsResponse {
  follow_ups: FollowUp[];
  count: number;
}

function FollowUpAlertsWidget() {
  const router = useRouter();
  const { data, isLoading, error } = useSWR<FollowUpsResponse>(
    "/follow-ups?status=waiting",
    (url: string) => api.get<FollowUpsResponse>(url),
    { refreshInterval: 2 * 60 * 1000 },
  );

  // "Overdue" is derived client-side from waiting items past their deadline —
  // the backend has no overdue status. GET /follow-ups doesn't paginate, so
  // filtering the full waiting set yields an accurate count.
  const overdueCount = (data?.follow_ups ?? []).filter((fu) => isOverdue(fu)).length;

  return (
    <Widget title="Follow-up Alerts" icon={AlertCircle}>
      {isLoading && (
        <div className="space-y-2">
          <SkeletonLine w="1/4" />
          <SkeletonLine w="1/2" />
        </div>
      )}

      {error && (
        <p className="text-xs text-slate-500">Could not load follow-ups.</p>
      )}

      {!isLoading && !error && overdueCount === 0 && (
        <p className="text-xs text-slate-500">
          No overdue follow-ups — great work!
        </p>
      )}

      {!isLoading && !error && overdueCount > 0 && (
        <>
          <p className="text-2xl font-bold text-orange-400">
            {overdueCount}
            <span className="ml-1.5 text-sm font-normal text-slate-400">
              overdue
            </span>
          </p>
          <button
            onClick={() => router.push("/follow-ups")}
            className="mt-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Review follow-ups →
          </button>
        </>
      )}
    </Widget>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-5 pb-6">
      {/* Morning briefing — full width, only shows when data exists */}
      <MorningBriefingCard />

      {/* 2 × 2 widget grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <PriorityInboxWidget />
        <TodaysScheduleWidget />
        <WaitingOnWidget />
        <FollowUpAlertsWidget />
      </div>
    </div>
  );
}
