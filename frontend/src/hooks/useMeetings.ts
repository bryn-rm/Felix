"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type {
  Meeting,
  MeetingDetail,
  MeetingTemplate,
} from "@/lib/types";

interface MeetingListResponse {
  meetings: Meeting[];
}

interface StartMeetingInput {
  template: MeetingTemplate;
  title?: string | null;
  calendar_event_id?: string | null;
}

/**
 * Meeting list + lifecycle mutations. Mirrors useJobs / useCommitments.
 *
 * A `processing` meeting becomes `done` in the background (summarization runs
 * off the request path), so the list polls on a modest interval to pick up the
 * transition without a manual refresh.
 */
export function useMeetings() {
  const { data, error, isLoading, mutate } = useSWR<MeetingListResponse>(
    "/meetings",
    (url: string) => api.get<MeetingListResponse>(url),
    { refreshInterval: 30 * 1000 },
  );

  async function startMeeting(input: StartMeetingInput): Promise<string> {
    const res = await api.post<{ meeting_id: string }>("/meetings/start", input);
    await mutate();
    return res.meeting_id;
  }

  async function endMeeting(id: string) {
    await api.post(`/meetings/${id}/end`);
    await mutate();
  }

  async function deleteMeeting(id: string) {
    await api.del(`/meetings/${id}`);
    await mutate();
  }

  return {
    meetings: data?.meetings ?? [],
    isLoading,
    error: error as Error | undefined,
    mutate,
    startMeeting,
    endMeeting,
    deleteMeeting,
  };
}

/** Single meeting: row + transcript segments + latest summary. */
export function useMeeting(id: string | null) {
  // A meeting still processing flips to done in the background — poll while we
  // wait so the detail page renders the summary as soon as it lands.
  const { data, error, isLoading, mutate } = useSWR<MeetingDetail>(
    id ? `/meetings/${id}` : null,
    (url: string) => api.get<MeetingDetail>(url),
    {
      refreshInterval: (latest) =>
        latest?.meeting?.status === "processing" ? 4 * 1000 : 0,
    },
  );

  async function saveNotes(content: string) {
    if (!id) return;
    await api.post(`/meetings/${id}/notes`, { content });
  }

  async function resummarize() {
    if (!id) return;
    await api.post(`/meetings/${id}/summarize`);
    await mutate();
  }

  return {
    detail: data,
    meeting: data?.meeting,
    segments: data?.segments ?? [],
    summary: data?.summary ?? null,
    isLoading,
    error: error as Error | undefined,
    mutate,
    saveNotes,
    resummarize,
  };
}
