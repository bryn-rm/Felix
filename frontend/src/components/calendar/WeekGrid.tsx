"use client";

import { useEffect, useState } from "react";
import type { CalendarEvent } from "@/lib/types";
import { EventCard } from "./EventCard";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const HOUR_HEIGHT = 64; // px per hour
const START_HOUR = 7; // 07:00
const END_HOUR = 22; // 22:00
const TOTAL_HOURS = END_HOUR - START_HOUR; // 15
const TOTAL_HEIGHT = TOTAL_HOURS * HOUR_HEIGHT; // 960 px
const MAX_COLS = 3; // max overlapping columns before overflow

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: TOTAL_HOURS }, (_, i) => START_HOUR + i);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function localDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function eventDateKey(iso: string): string {
  return localDateKey(new Date(iso));
}

function eventTop(start: string): number {
  const d = new Date(start);
  const mins = (d.getHours() - START_HOUR) * 60 + d.getMinutes();
  return Math.max(0, (mins / 60) * HOUR_HEIGHT);
}

function eventHeight(start: string, end: string): number {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  const hours = ms / 3_600_000;
  return Math.max(18, hours * HOUR_HEIGHT);
}

function detectConflicts(events: CalendarEvent[]): Set<string> {
  const ids = new Set<string>();
  for (let i = 0; i < events.length; i++) {
    for (let j = i + 1; j < events.length; j++) {
      const aS = new Date(events[i].start).getTime();
      const aE = new Date(events[i].end).getTime();
      const bS = new Date(events[j].start).getTime();
      const bE = new Date(events[j].end).getTime();
      if (aS < bE && aE > bS) {
        ids.add(events[i].id);
        ids.add(events[j].id);
      }
    }
  }
  return ids;
}

interface LayoutItem {
  event: CalendarEvent;
  top: number;
  height: number;
  colIndex: number;
  colCount: number;
  hidden: boolean;
}

function layoutDayEvents(events: CalendarEvent[]): {
  layouts: LayoutItem[];
  overflowByHour: Map<number, number>;
} {
  if (events.length === 0)
    return { layouts: [], overflowByHour: new Map() };

  const sorted = [...events].sort(
    (a, b) => new Date(a.start).getTime() - new Date(b.start).getTime(),
  );

  // Greedy column assignment
  const colEnds: number[] = []; // end-time ms of last event per column
  const raw = sorted.map((event) => {
    const start = new Date(event.start).getTime();
    const end = new Date(event.end).getTime();
    let colIndex = colEnds.findIndex((e) => e <= start);
    if (colIndex === -1) {
      colIndex = colEnds.length;
      colEnds.push(end);
    } else {
      colEnds[colIndex] = end;
    }
    return { event, colIndex, startMs: start, endMs: end };
  });

  // Resolve colCount per event (max colIndex among overlapping peers + 1)
  const withColCount = raw.map((item) => {
    const overlapping = raw.filter(
      (o) => o.startMs < item.endMs && o.endMs > item.startMs,
    );
    const colCount = Math.min(
      MAX_COLS,
      Math.max(...overlapping.map((o) => o.colIndex)) + 1,
    );
    return { ...item, colCount };
  });

  // Track overflow per start-hour
  const overflowByHour = new Map<number, number>();
  const layouts: LayoutItem[] = withColCount.map((item) => {
    const hidden = item.colIndex >= MAX_COLS;
    if (hidden) {
      const hour = new Date(item.event.start).getHours();
      overflowByHour.set(hour, (overflowByHour.get(hour) ?? 0) + 1);
    }
    return {
      event: item.event,
      top: eventTop(item.event.start),
      height: eventHeight(item.event.start, item.event.end),
      colIndex: item.colIndex,
      colCount: item.colCount,
      hidden,
    };
  });

  return { layouts, overflowByHour };
}

// ---------------------------------------------------------------------------
// WeekGrid
// ---------------------------------------------------------------------------

interface WeekGridProps {
  events: CalendarEvent[];
  weekStart: Date;
  onEventClick?: (event: CalendarEvent) => void;
}

export function WeekGrid({ events, weekStart, onEventClick }: WeekGridProps) {
  // Mon-Sun dates for this week
  const weekDates = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  const todayKey = localDateKey(new Date());

  // Live current-time indicator
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);

  const currentTop =
    (now.getHours() - START_HOUR + now.getMinutes() / 60) * HOUR_HEIGHT;
  const showCurrentTime =
    now.getHours() >= START_HOUR && now.getHours() < END_HOUR;

  return (
    <div className="flex overflow-hidden rounded-lg border border-slate-700 bg-slate-900">
      {/* ---- Time label column ---- */}
      <div className="flex w-12 shrink-0 flex-col border-r border-slate-700">
        {/* Header spacer */}
        <div className="h-10 shrink-0 border-b border-slate-700" />
        {/* Labels */}
        <div className="relative shrink-0" style={{ height: TOTAL_HEIGHT }}>
          {HOURS.map((h) => (
            <div
              key={h}
              className="absolute right-1 text-[10px] leading-none text-slate-500"
              style={{ top: (h - START_HOUR) * HOUR_HEIGHT - 6 }}
            >
              {h % 12 === 0 ? "12" : h % 12}
              <span className="text-[9px]">{h < 12 ? "a" : "p"}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ---- Day columns ---- */}
      <div className="flex flex-1 overflow-x-auto">
        {weekDates.map((date, dayIdx) => {
          const dateKey = localDateKey(date);
          const isToday = dateKey === todayKey;
          const dayEvents = events.filter(
            (e) => eventDateKey(e.start) === dateKey,
          );
          const conflictIds = detectConflicts(dayEvents);
          const { layouts, overflowByHour } = layoutDayEvents(dayEvents);

          return (
            <div
              key={dateKey}
              className={`flex min-w-[72px] flex-1 flex-col border-r border-slate-700 last:border-r-0 ${
                isToday ? "bg-slate-800/50" : ""
              }`}
            >
              {/* Day header */}
              <div
                className={`flex h-10 shrink-0 flex-col items-center justify-center border-b border-slate-700 ${
                  isToday ? "text-indigo-400" : "text-slate-500"
                }`}
              >
                <span className="text-[10px] font-medium uppercase tracking-wide">
                  {DAYS[dayIdx]}
                </span>
                <span
                  className={`text-sm font-semibold leading-none ${
                    isToday
                      ? "flex h-5 w-5 items-center justify-center rounded-full bg-indigo-500 text-white text-xs"
                      : ""
                  }`}
                >
                  {date.getDate()}
                </span>
              </div>

              {/* Events area */}
              <div
                className="relative shrink-0"
                style={{ height: TOTAL_HEIGHT }}
              >
                {/* Hourly grid lines */}
                {HOURS.map((h) => (
                  <div
                    key={h}
                    className="pointer-events-none absolute inset-x-0 border-t border-slate-700/40"
                    style={{ top: (h - START_HOUR) * HOUR_HEIGHT }}
                  />
                ))}

                {/* Current time line */}
                {isToday && showCurrentTime && (
                  <div
                    className="pointer-events-none absolute inset-x-0 z-20 flex items-center"
                    style={{ top: currentTop }}
                  >
                    <div className="-ml-1 h-2 w-2 shrink-0 rounded-full bg-red-500" />
                    <div className="flex-1 border-t-2 border-red-500" />
                  </div>
                )}

                {/* Events */}
                {layouts
                  .filter((l) => !l.hidden)
                  .map(({ event, top, height, colIndex, colCount }) => {
                    const clampedHeight = Math.min(
                      height,
                      TOTAL_HEIGHT - top,
                    );
                    const leftPct = (colIndex / colCount) * 100;
                    const widthPct = (1 / colCount) * 100;
                    const compact =
                      clampedHeight < 36 || colCount > 1;

                    return (
                      <div
                        key={event.id}
                        className="absolute p-0.5"
                        style={{
                          top,
                          height: clampedHeight,
                          left: `${leftPct}%`,
                          width: `${widthPct}%`,
                          zIndex: 10,
                        }}
                      >
                        <EventCard
                          event={event}
                          hasConflict={conflictIds.has(event.id)}
                          compact={compact}
                          onOpenDetail={onEventClick}
                        />
                      </div>
                    );
                  })}

                {/* "+N more" overflow chips */}
                {Array.from(overflowByHour.entries()).map(([hour, count]) => {
                  const top =
                    (hour - START_HOUR + 1) * HOUR_HEIGHT - 18;
                  return (
                    <div
                      key={`overflow-${hour}`}
                      className="absolute right-1 z-10 rounded bg-slate-700/80 px-1.5 py-0.5 text-[10px] text-slate-400"
                      style={{ top }}
                    >
                      +{count} more
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
