"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type {
  JobApplication,
  JobBoard,
  JobEvent,
  JobStatus,
  JobSuggestion,
} from "@/lib/types";

interface SuggestionsResponse {
  suggestions: JobSuggestion[];
  count: number;
}

interface JobCreateInput {
  company: string;
  role_title: string;
  location?: string | null;
  job_url?: string | null;
  status?: JobStatus;
  contact_name?: string | null;
  contact_email?: string | null;
  compensation?: string | null;
  notes?: string | null;
}

/** Kanban board + suggestions, with mutations that revalidate both. */
export function useJobs() {
  const board = useSWR<JobBoard>(
    "/jobs",
    (url: string) => api.get<JobBoard>(url),
    { refreshInterval: 5 * 60 * 1000 },
  );

  const suggestions = useSWR<SuggestionsResponse>(
    "/jobs/suggestions",
    (url: string) => api.get<SuggestionsResponse>(url),
    { refreshInterval: 5 * 60 * 1000 },
  );

  async function refresh() {
    await Promise.all([board.mutate(), suggestions.mutate()]);
  }

  async function addJob(input: JobCreateInput): Promise<JobApplication | null> {
    const res = await api.post<{ job: JobApplication | null }>("/jobs", input);
    await board.mutate();
    return res.job;
  }

  async function moveJob(id: string, status: JobStatus) {
    await api.patch(`/jobs/${id}`, { status });
    await board.mutate();
  }

  async function updateJob(id: string, patch: Partial<JobApplication>) {
    await api.patch(`/jobs/${id}`, patch);
    await board.mutate();
  }

  async function deleteJob(id: string) {
    await api.del(`/jobs/${id}`);
    await board.mutate();
  }

  async function resolveSuggestion(id: string, accept: boolean) {
    await api.post(`/jobs/suggestions/${id}`, { accept });
    await refresh();
  }

  return {
    board: board.data,
    isLoading: board.isLoading,
    error: board.error as Error | undefined,
    suggestions: suggestions.data?.suggestions ?? [],
    mutate: board.mutate,
    refresh,
    addJob,
    moveJob,
    updateJob,
    deleteJob,
    resolveSuggestion,
  };
}

interface JobDetailResponse {
  job: JobApplication;
  events: JobEvent[];
}

/** Single job + its event timeline. */
export function useJob(id: string | null) {
  const { data, error, isLoading, mutate } = useSWR<JobDetailResponse>(
    id ? `/jobs/${id}` : null,
    (url: string) => api.get<JobDetailResponse>(url),
  );

  async function addNote(detail: string) {
    if (!id) return;
    await api.post(`/jobs/${id}/events`, { event_type: "note", detail });
    await mutate();
  }

  async function updateJob(patch: Partial<JobApplication>) {
    if (!id) return;
    await api.patch(`/jobs/${id}`, patch);
    await mutate();
  }

  async function draftFollowUp(): Promise<{
    draft: { id: string; draft_text: string } | null;
    email_id?: string;
    reason?: string;
  }> {
    if (!id) return { draft: null };
    return api.post(`/jobs/${id}/draft-follow-up`);
  }

  return {
    job: data?.job,
    events: data?.events ?? [],
    isLoading,
    error: error as Error | undefined,
    mutate,
    addNote,
    updateJob,
    draftFollowUp,
  };
}
