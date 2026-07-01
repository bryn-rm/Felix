"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Radio, Square } from "lucide-react";

import { ApiError } from "@/lib/api";
import {
  isMeetingCaptureSupported,
  useMeetingCapture,
} from "@/hooks/useMeetingCapture";
import { useMeeting, useMeetings } from "@/hooks/useMeetings";
import { LiveTranscript } from "@/components/meetings/LiveTranscript";
import {
  NotesEditor,
  type NotesEditorHandle,
} from "@/components/meetings/NotesEditor";
import { RecordingIndicator } from "@/components/meetings/RecordingIndicator";

interface PageProps {
  params: { id: string };
}

export default function LiveMeetingPage({ params }: PageProps) {
  const { id } = params;
  const router = useRouter();
  const { meeting, saveNotes } = useMeeting(id);
  const { endMeeting } = useMeetings();

  const [supported] = useState(() => isMeetingCaptureSupported());
  const finalizeRef = useRef<() => void>(() => {});
  const finalizingRef = useRef(false);
  const notesEditorRef = useRef<NotesEditorHandle | null>(null);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);

  const { status, error, liveTranscript, interim, begin, stop, failCapture } =
    useMeetingCapture(id, { onShareEnded: () => finalizeRef.current() });

  const finalize = useCallback(async () => {
    if (finalizingRef.current) return;
    finalizingRef.current = true;
    setFinalizeError(null);
    try {
      await notesEditorRef.current?.flush();
    } catch {
      finalizingRef.current = false;
      setFinalizeError("Could not save notes before summarizing. Try again.");
      return;
    }
    // Wait for the server to flush + persist the final STT segments (stop()
    // resolves when the socket closes) BEFORE summarizing, so the summary can't
    // miss the tail of the meeting.
    await stop();
    try {
      await endMeeting(id);
    } catch (e) {
      // Discriminate — don't blanket-swallow. A 404 means the meeting
      // legitimately already ended (the auto-end sweep / a race): benign, fall
      // through to the detail page as before. Any OTHER failure (429 over
      // budget, 500, network) means the row is still 'recording' and
      // summarization never started — stop() has already closed the socket +
      // media, so navigating on would dead-end the user on a blank
      // recording-status page. Route it through the shared failCapture sink so
      // they get a rendered error + retry instead.
      if (!(e instanceof ApiError && e.status === 404)) {
        finalizingRef.current = false; // allow a fresh Stop attempt after retry
        failCapture(
          e instanceof ApiError && e.status === 429
            ? "You’ve reached your monthly AI limit, so this meeting wasn’t summarized. You can retry once your limit resets."
            : "Couldn’t finish the meeting. Please try again.",
        );
        return;
      }
    }
    router.push(`/meetings/${id}`);
  }, [stop, endMeeting, id, router, failCapture]);

  useEffect(() => {
    finalizeRef.current = finalize;
  }, [finalize]);

  const recording = status === "recording" || status === "reconnecting";
  const canBegin = status === "idle" || status === "error";
  const connecting = status === "requesting" || status === "connecting";

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link
            href="/meetings"
            className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
            aria-label="Back to meetings"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-slate-100">
              {meeting?.title || "Untitled meeting"}
            </h1>
            <RecordingIndicator status={status} />
          </div>
        </div>

        {recording && (
          <button
            onClick={finalize}
            className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500"
          >
            <Square className="h-4 w-4" />
            Stop &amp; summarize
          </button>
        )}
      </div>

      {(error || finalizeError) && (
        <p className="text-sm text-red-400">{finalizeError ?? error}</p>
      )}

      {!supported && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
          Meeting capture works in Google Chrome on desktop — tab-audio sharing
          isn’t available in this browser.
        </p>
      )}

      {/* Pre-start / connecting: instructions + begin button (or a spinner) */}
      {!recording ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
          <div className="max-w-md space-y-2">
            <p className="text-base font-medium text-slate-200">
              Ready to capture
            </p>
            <p className="text-sm text-slate-500">
              When you click start, Chrome asks you to pick a tab to share —
              choose your meeting tab and tick{" "}
              <strong className="text-slate-300">“Also share tab audio”</strong>.
              Use headphones to keep the two channels clean.
            </p>
          </div>
          {canBegin ? (
            <button
              onClick={begin}
              disabled={!supported}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              <Radio className="h-4 w-4" />
              {status === "error" ? "Try again" : "Start recording"}
            </button>
          ) : (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              {connecting ? "Starting…" : "Finishing up…"}
            </div>
          )}
        </div>
      ) : (
        /* Live: transcript + notes side by side */
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="flex min-h-0 flex-col rounded-lg border border-slate-700/50 bg-slate-800/20 p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
              Live transcript
            </p>
            <div className="min-h-0 flex-1">
              <LiveTranscript lines={liveTranscript} interim={interim} />
            </div>
          </div>
          <div className="min-h-0">
            {/* Seed from saved notes so a refresh/reconnect mid-meeting doesn't
                overwrite what was already autosaved. */}
            <NotesEditor
              ref={notesEditorRef}
              initialValue={meeting?.user_notes ?? ""}
              onSave={saveNotes}
            />
          </div>
        </div>
      )}
    </div>
  );
}
