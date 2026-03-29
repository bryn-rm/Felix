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

export function useCalendar(_weekStart: Date) {
  const { data, error, isLoading } = useSWR<{ events: CalendarEvent[]; count: number }>(
    `/calendar/events?days_ahead=14`,
    (url: string) => api.get<{ events: CalendarEvent[]; count: number }>(url),
  );

  return {
    events: data?.events ?? [],
    isLoading,
    error: error as Error | undefined,
  };
}
