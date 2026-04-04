"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { CalendarEvent } from "@/lib/types";

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

export function useCalendar(weekStart: Date) {
  const weekEndExclusive = addDays(weekStart, 7);
  const url = `/calendar/events?time_min=${encodeURIComponent(weekStart.toISOString())}&time_max=${encodeURIComponent(weekEndExclusive.toISOString())}`;

  const { data, error, isLoading } = useSWR<{ events: CalendarEvent[]; count: number }>(
    url,
    (url: string) => api.get<{ events: CalendarEvent[]; count: number }>(url),
  );

  return {
    events: data?.events ?? [],
    isLoading,
    error: error as Error | undefined,
  };
}
