"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { CalendarEvent } from "@/lib/types";

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function useCalendar(weekStart: Date) {
  const start = isoDate(weekStart);
  const end = isoDate(addDays(weekStart, 6));

  const { data, error, isLoading } = useSWR<CalendarEvent[]>(
    `/calendar/events?start=${start}&end=${end}`,
    (url: string) => api.get<CalendarEvent[]>(url),
  );

  return {
    events: data ?? [],
    isLoading,
    error: error as Error | undefined,
  };
}
