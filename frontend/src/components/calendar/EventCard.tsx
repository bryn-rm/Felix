"use client";

import { useState } from "react";
import { MapPin, Users, FileText, ChevronDown, ChevronUp } from "lucide-react";
import type { CalendarEvent } from "@/lib/types";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

interface EventCardProps {
  event: CalendarEvent;
  hasConflict?: boolean;
  /** Compact mode: no time row, no expand chevron */
  compact?: boolean;
}

export function EventCard({ event, hasConflict, compact }: EventCardProps) {
  const [expanded, setExpanded] = useState(false);

  const startTime = formatTime(event.start);
  const endTime = formatTime(event.end);

  const baseBg = event.is_focus_block
    ? "bg-indigo-900/80 border-indigo-700/60"
    : "bg-slate-700/80 border-slate-600/60";

  const conflictBorder = hasConflict ? "border-l-2 border-l-amber-400" : "";

  return (
    <div
      className={`h-full overflow-hidden rounded border px-1.5 py-1 text-xs cursor-pointer transition-all hover:brightness-110 select-none ${baseBg} ${conflictBorder}`}
      onClick={() => !compact && setExpanded((v) => !v)}
    >
      {/* Title row */}
      <div className="flex items-start justify-between gap-1">
        <p className="font-medium text-slate-100 truncate leading-tight">
          {event.is_focus_block ? "Focus time" : event.title}
        </p>
        {!compact && (
          <span className="shrink-0 text-slate-400 mt-0.5">
            {expanded ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
          </span>
        )}
      </div>

      {/* Time row */}
      {!compact && (
        <p className="text-slate-400 mt-0.5 truncate">
          {startTime}–{endTime}
        </p>
      )}

      {/* Attendee count badge (always shown if space) */}
      {!compact && event.attendees.length > 0 && !expanded && (
        <p className="mt-0.5 text-slate-500">
          <Users className="inline h-2.5 w-2.5 mr-0.5" />
          {event.attendees.length}
        </p>
      )}

      {/* Expanded detail panel */}
      {expanded && (
        <div className="mt-1.5 space-y-1 border-t border-slate-600/60 pt-1.5">
          <p className="text-slate-400">
            {startTime} – {endTime}
          </p>

          {event.location && (
            <div className="flex items-center gap-1 text-slate-300">
              <MapPin className="h-3 w-3 shrink-0" />
              <span className="truncate">{event.location}</span>
            </div>
          )}

          {event.attendees.length > 0 && (
            <div className="flex items-center gap-1 text-slate-300">
              <Users className="h-3 w-3 shrink-0" />
              <span>
                {event.attendees.length} attendee
                {event.attendees.length !== 1 ? "s" : ""}
              </span>
            </div>
          )}

          {event.description && (
            <div className="flex items-start gap-1 text-indigo-400">
              <FileText className="h-3 w-3 shrink-0 mt-0.5" />
              <span className="text-slate-400 line-clamp-3">
                {event.description}
              </span>
            </div>
          )}

          {/* Meeting notes link — shown when description exists (used as proxy for notes) */}
          {event.description && (
            <p className="flex items-center gap-1 text-indigo-400 hover:underline cursor-pointer">
              <FileText className="h-3 w-3 shrink-0" />
              Meeting notes
            </p>
          )}
        </div>
      )}
    </div>
  );
}
