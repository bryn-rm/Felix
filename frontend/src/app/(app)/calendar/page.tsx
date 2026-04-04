"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useCalendar } from "@/hooks/useCalendar";
import { WeekGrid } from "@/components/calendar/WeekGrid";
import { api } from "@/lib/api";
import type { CalendarEvent } from "@/lib/types";

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function getMonday(date: Date): Date {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  const day = d.getDay(); // 0 = Sun
  d.setDate(d.getDate() + (day === 0 ? -6 : 1 - day));
  return d;
}

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

function formatWeekRange(weekStart: Date): string {
  const weekEnd = addDays(weekStart, 6);
  const monthDay = (d: Date) =>
    d.toLocaleDateString("en", { month: "short", day: "numeric" });
  return `${monthDay(weekStart)} – ${weekEnd.toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })}`;
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function SkeletonGrid() {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-700">
      <div className="flex">
        <div className="w-12 shrink-0 border-r border-slate-700" />
        {Array.from({ length: 7 }).map((_, i) => (
          <div
            key={i}
            className="h-10 flex-1 animate-pulse border-b border-r border-slate-700 bg-slate-800/60 last:border-r-0"
          />
        ))}
      </div>
      <div className="flex" style={{ height: 480 }}>
        <div className="w-12 shrink-0 border-r border-slate-700 bg-slate-900/20" />
        {Array.from({ length: 7 }).map((_, i) => (
          <div
            key={i}
            className="flex-1 border-r border-slate-700 bg-slate-900/10 last:border-r-0 p-1"
          >
            {i % 3 === 0 && (
              <div
                className="mt-6 h-12 animate-pulse rounded bg-slate-700/40"
                style={{ animationDelay: `${i * 100}ms` }}
              />
            )}
            {i % 2 === 1 && (
              <div
                className="mt-24 h-8 animate-pulse rounded bg-indigo-900/30"
                style={{ animationDelay: `${i * 150}ms` }}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Free-slot modal
// ---------------------------------------------------------------------------

interface FreeSlot {
  start: string;
  end: string;
}

function FreeSlotModal({ onClose }: { onClose: () => void }) {
  const [slots, setSlots] = useState<FreeSlot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ slots: FreeSlot[] }>(`/calendar/free-slots?duration_minutes=30&days_ahead=1`)
      .then((r) => setSlots(r.slots))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to find free slots."))
      .finally(() => setLoading(false));
  }, []);

  function formatSlot(slot: FreeSlot): string {
    const fmt = (iso: string) =>
      new Date(iso).toLocaleTimeString("en", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      });
    return `${fmt(slot.start)} – ${fmt(slot.end)}`;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
        <h2 className="mb-1 text-base font-semibold text-slate-100">
          Free 30-min slots — today
        </h2>
        <p className="mb-4 text-xs text-slate-500">
          Click a slot to copy the time.
        </p>

        {loading && (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-10 animate-pulse rounded-lg bg-slate-700/60"
              />
            ))}
          </div>
        )}

        {error && (
          <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">
            {error}
          </p>
        )}

        {slots && slots.length === 0 && (
          <p className="text-sm text-slate-400">
            No free slots available today.
          </p>
        )}

        {slots && slots.length > 0 && (
          <div className="space-y-2">
            {slots.map((slot, i) => (
              <button
                key={i}
                onClick={() =>
                  navigator.clipboard?.writeText(formatSlot(slot))
                }
                className="w-full rounded-lg border border-slate-600 bg-slate-700/50 px-4 py-2.5 text-left text-sm text-slate-200 transition-colors hover:bg-slate-700 active:scale-[0.98]"
              >
                {formatSlot(slot)}
              </button>
            ))}
          </div>
        )}

        <button
          onClick={onClose}
          className="mt-4 w-full rounded-lg bg-slate-700 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-600"
        >
          Close
        </button>
      </div>
    </div>
  );
}

function EventDetailModal({
  event,
  onClose,
}: {
  event: CalendarEvent;
  onClose: () => void;
}) {
  const formatDate = (iso: string) =>
    new Date(iso).toLocaleString("en", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "short",
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
        <h2 className="text-lg font-semibold text-slate-100">{event.title}</h2>
        <p className="mt-1 text-sm text-slate-400">
          {formatDate(event.start)} → {formatDate(event.end)}
        </p>

        {event.location && (
          <p className="mt-4 text-sm text-slate-200">
            <span className="text-slate-400">Location:</span> {event.location}
          </p>
        )}

        <p className="mt-2 text-sm text-slate-200">
          <span className="text-slate-400">Attendees:</span>{" "}
          {event.attendees.length > 0 ? event.attendees.join(", ") : "None"}
        </p>

        {event.organizer && (
          <p className="mt-2 text-sm text-slate-200">
            <span className="text-slate-400">Organizer:</span> {event.organizer}
          </p>
        )}

        {event.description && (
          <div className="mt-4 rounded-lg border border-slate-700 bg-slate-900/50 p-3">
            <p className="whitespace-pre-wrap text-sm text-slate-300">
              {event.description}
            </p>
          </div>
        )}

        {(event.hangout_link || event.html_link) && (
          <div className="mt-4 flex flex-wrap gap-2">
            {event.hangout_link && (
              <a
                href={event.hangout_link}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
              >
                Join meeting
              </a>
            )}
            {event.html_link && (
              <a
                href={event.html_link}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-200 hover:border-slate-500"
              >
                Open in Google Calendar
              </a>
            )}
          </div>
        )}

        <button
          onClick={onClose}
          className="mt-6 w-full rounded-lg bg-slate-700 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-600"
        >
          Close
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CalendarPage() {
  const [weekStart, setWeekStart] = useState(() => getMonday(new Date()));
  const [showFreeSlot, setShowFreeSlot] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | null>(null);

  const { events, isLoading, error } = useCalendar(weekStart);

  const isCurrentWeek =
    weekStart.toISOString().slice(0, 10) ===
    getMonday(new Date()).toISOString().slice(0, 10);

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setWeekStart((d) => addDays(d, -7))}
            aria-label="Previous week"
            className="rounded-lg border border-slate-600 bg-slate-800 p-1.5 text-slate-400 transition-colors hover:text-slate-100"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>

          <h1 className="w-56 text-center text-sm font-semibold text-slate-100">
            {formatWeekRange(weekStart)}
          </h1>

          <button
            onClick={() => setWeekStart((d) => addDays(d, 7))}
            aria-label="Next week"
            className="rounded-lg border border-slate-600 bg-slate-800 p-1.5 text-slate-400 transition-colors hover:text-slate-100"
          >
            <ChevronRight className="h-4 w-4" />
          </button>

          {!isCurrentWeek && (
            <button
              onClick={() => setWeekStart(getMonday(new Date()))}
              className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:text-slate-100"
            >
              Today
            </button>
          )}
        </div>

        <button
          onClick={() => setShowFreeSlot(true)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          <Search className="h-4 w-4" />
          Find free slot
        </button>
      </div>

      {/* Error */}
      {error && (
        <p className="text-sm text-red-400">
          Failed to load calendar: {error.message}
        </p>
      )}

      {/* Grid */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <SkeletonGrid />
        ) : (
          <WeekGrid
            events={events}
            weekStart={weekStart}
            onEventClick={(event) => setSelectedEvent(event)}
          />
        )}
      </div>

      {showFreeSlot && (
        <FreeSlotModal onClose={() => setShowFreeSlot(false)} />
      )}

      {selectedEvent && (
        <EventDetailModal
          event={selectedEvent}
          onClose={() => setSelectedEvent(null)}
        />
      )}
    </div>
  );
}
