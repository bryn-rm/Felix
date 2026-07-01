"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Radio } from "lucide-react";

import { useMeetings } from "@/hooks/useMeetings";
import { MeetingList } from "@/components/meetings/MeetingList";
import { StartCaptureModal } from "@/components/meetings/StartCaptureModal";
import type { MeetingTemplate } from "@/lib/types";

export default function MeetingsPage() {
  const router = useRouter();
  const { meetings, isLoading, error, startMeeting, deleteMeeting } = useMeetings();
  const [modalOpen, setModalOpen] = useState(false);

  async function handleStart(template: MeetingTemplate, title: string) {
    const id = await startMeeting({ template, title: title || null });
    setModalOpen(false);
    router.push(`/meetings/live/${id}`);
  }

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Meetings</h1>
          <p className="mt-1 text-sm text-slate-500">
            Capture an in-browser meeting and Felix writes the enhanced notes,
            decisions, and action items.
          </p>
        </div>
        <button
          onClick={() => setModalOpen(true)}
          className="flex shrink-0 items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          <Radio className="h-4 w-4" />
          Start capture
        </button>
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
            Hit “Start capture”, share the meeting tab with audio, and Felix takes
            it from there.
          </p>
        </div>
      )}

      {!isLoading && !error && meetings.length > 0 && (
        <MeetingList meetings={meetings} onDelete={deleteMeeting} />
      )}

      <StartCaptureModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onStart={handleStart}
      />
    </div>
  );
}
