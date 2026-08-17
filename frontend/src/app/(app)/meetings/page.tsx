"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Radio, Sparkles } from "lucide-react";

import { api } from "@/lib/api";
import { useMeetings } from "@/hooks/useMeetings";
import { MeetingList } from "@/components/meetings/MeetingList";
import { StartCaptureModal } from "@/components/meetings/StartCaptureModal";
import type { Settings as UserSettings, StartMeetingInput } from "@/lib/types";

export default function MeetingsPage() {
  const router = useRouter();
  const { meetings, isLoading, error, startMeeting, deleteMeeting } = useMeetings();
  const [startMode, setStartMode] = useState<"capture" | "manual" | null>(null);

  // Fail closed, the same way the Meetings nav item does for meeting_capture_mode:
  // a manual session's ONLY function is the assistant, so without the flag this
  // button would create a real meeting row that lands on a page offering nothing
  // but "turn on Live assist" — and now needs finishing or deleting.
  const { data: settings } = useSWR<UserSettings>("/settings", (url: string) =>
    api.get<UserSettings>(url),
  );
  const assistEnabled = settings?.live_assist_mode === true;

  async function handleStart(input: StartMeetingInput) {
    const id = await startMeeting(input);
    setStartMode(null);
    router.push(`/meetings/live/${id}`);
  }

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Meetings</h1>
          <p className="mt-1 text-sm text-slate-500">
            {assistEnabled
              ? "Record an online call, or open the assistant on its own for manual notes and questions."
              : "Record an online call and Felix writes up the summary."}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          {assistEnabled && (
            <button
              onClick={() => setStartMode("manual")}
              className="flex items-center gap-2 rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-indigo-500 hover:text-indigo-200"
            >
              <Sparkles className="h-4 w-4" />
              Open assistant
            </button>
          )}
          <button
            onClick={() => setStartMode("capture")}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
          >
            <Radio className="h-4 w-4" />
            Start capture
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-16 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
              style={{ animationDelay: `${i * 80}ms` }}
            />
          ))}
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400">
          Failed to load meetings: {error.message}
        </p>
      )}

      {!isLoading && !error && meetings.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <p className="text-base font-medium text-slate-300">No meetings yet</p>
          <p className="max-w-sm text-sm text-slate-500">
            {assistEnabled
              ? "Start a capture for an online call, or open the assistant for an in-person meeting and take notes yourself."
              : "Start a capture for an online call and Felix takes it from there."}
          </p>
        </div>
      )}

      {!isLoading && !error && meetings.length > 0 && (
        <MeetingList meetings={meetings} onDelete={deleteMeeting} />
      )}

      <StartCaptureModal
        open={startMode !== null}
        onClose={() => setStartMode(null)}
        onStart={handleStart}
        mode={startMode ?? "capture"}
      />
    </div>
  );
}
