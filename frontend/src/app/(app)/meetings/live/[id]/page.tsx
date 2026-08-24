"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, Check, FileText, Loader2, Radio, Sparkles, Square } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { Settings as UserSettings } from "@/lib/types";
import { useAssistItems } from "@/hooks/useAssistItems";
import { useAssistAsk } from "@/hooks/useAssistAsk";
import { isMeetingCaptureSupported } from "@/lib/capture-support";
import { useMeetingCapture } from "@/hooks/useMeetingCapture";
import { useMeeting, useMeetings } from "@/hooks/useMeetings";
import { AssistSidebar } from "@/components/meetings/AssistSidebar";
import { LiveTranscript } from "@/components/meetings/LiveTranscript";
import {
  NotesEditor,
  type NotesEditorHandle,
} from "@/components/meetings/NotesEditor";
import { RecordingIndicator } from "@/components/meetings/RecordingIndicator";
import {
  assistModeLabel,
  usesCandidateInterviewAssist,
} from "@/components/meetings/constants";

interface PageProps {
  params: { id: string };
}

export default function LiveMeetingPage({ params }: PageProps) {
  const { id } = params;
  const router = useRouter();
  const { meeting, isLoading, saveNotes } = useMeeting(id);
  const { endMeeting } = useMeetings();

  const [supported] = useState(() => isMeetingCaptureSupported());
  const finalizeRef = useRef<() => void>(() => {});
  const finalizingRef = useRef(false);
  const notesEditorRef = useRef<NotesEditorHandle | null>(null);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);

  const {
    status,
    error,
    liveTranscript,
    interim,
    assistItems,
    sendAsk,
    askPending,
    askError,
    begin,
    stop,
    failCapture,
  } = useMeetingCapture(id, { onShareEnded: () => finalizeRef.current() });

  // Live assist — fails closed: without the flag the sidebar (and its fetch)
  // never exists. Collapsed by default; the badge counts unseen cards.
  const { data: settings } = useSWR<UserSettings>("/settings", (url: string) =>
    api.get<UserSettings>(url),
  );
  const assistEnabled = settings?.live_assist_mode === true;
  // `meeting` is undefined until the row loads. Deriving the mode from it means
  // BOTH branches are wrong during that window, so nothing that acts on the
  // mode may render until `loaded` — see the loading guard below.
  const loaded = meeting !== undefined;
  const manualMode = meeting?.source === "manual_notes";
  const manualAssist = useAssistAsk(id, manualMode && assistEnabled);
  const [assistOpen, setAssistOpen] = useState(false);
  const [assistSeen, setAssistSeen] = useState(0);
  const incomingAssistItems = manualMode ? manualAssist.items : assistItems;
  const { items: cards, dismiss } = useAssistItems(id, incomingAssistItems, {
    enabled: assistEnabled,
  });
  // The badge only exists on the capture branch (manual mode shows the sidebar
  // permanently, with no toggle to carry a count), so this tracking is scoped
  // to it rather than running every render to feed something unreachable.
  const badgeActive = loaded && !manualMode && assistEnabled;
  useEffect(() => {
    if (assistOpen) setAssistSeen(cards.length);
  }, [assistOpen, cards.length]);
  const unseenCards =
    badgeActive && !assistOpen ? Math.max(0, cards.length - assistSeen) : 0;
  // Both derive from one resolver: a legacy interview row runs candidate
  // assist, so it must carry a badge too rather than doing it invisibly.
  const candidateInterviewAssist = usesCandidateInterviewAssist(meeting);
  const activeMode = assistModeLabel(meeting);

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
    if (!manualMode) await stop();
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
        const message =
          e instanceof ApiError && e.status === 429
            ? "You’ve reached your monthly AI limit, so this meeting wasn’t summarized. You can retry once your limit resets."
            : "Couldn’t finish the meeting. Please try again.";
        if (manualMode) setFinalizeError(message);
        else failCapture(message);
        return;
      }
    }
    router.push(`/meetings/${id}`);
  }, [stop, endMeeting, id, router, failCapture, manualMode]);

  useEffect(() => {
    finalizeRef.current = finalize;
  }, [finalize]);

  const recording = status === "recording" || status === "reconnecting";
  const manualSessionOpen = manualMode && meeting?.status === "recording";
  // Which signal means "this session is live" depends on the branch: capture
  // reads the socket, manual reads the row. ORing them would offer a manual
  // session a Finish button on a row that has already been summarized.
  const sessionOpen = manualMode ? manualSessionOpen : recording;
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
            <div className="flex items-center gap-2">
              {/* Same reason as the body below: until `source` lands, a recording
                  indicator on a manual session would be a lie about the one
                  thing that flow promises. */}
              {!loaded ? null : manualMode ? (
                <span className="flex items-center gap-1 text-xs text-emerald-300">
                  <FileText className="h-3.5 w-3.5" />
                  Manual notes · no recording
                </span>
              ) : (
                <RecordingIndicator status={status} />
              )}
              {activeMode && (
                <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-xs text-indigo-300">
                  {activeMode}
                </span>
              )}
            </div>
          </div>
        </div>

        {loaded && sessionOpen && (
          <div className="flex items-center gap-2">
            {badgeActive && (
              <button
                onClick={() => setAssistOpen((open) => !open)}
                aria-label="Toggle live assist"
                aria-pressed={assistOpen}
                className={`relative flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                  assistOpen
                    ? "border-indigo-500 bg-indigo-600/20 text-indigo-200"
                    : "border-slate-700 text-slate-300 hover:border-slate-500"
                }`}
              >
                <Sparkles className="h-4 w-4" />
                Assist
                {unseenCards > 0 && (
                  <span className="ml-0.5 rounded-full bg-indigo-600 px-1.5 text-[10px] font-semibold text-white">
                    {unseenCards}
                  </span>
                )}
              </button>
            )}
            <button
              onClick={finalize}
              className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                manualMode
                  ? "bg-indigo-600 hover:bg-indigo-500"
                  : "bg-red-600 hover:bg-red-500"
              }`}
            >
              {manualMode ? (
                <Check className="h-4 w-4" />
              ) : (
                <Square className="h-4 w-4" />
              )}
              {manualMode ? "Finish & summarize" : "Stop & summarize"}
            </button>
          </div>
        )}
      </div>

      {(error || finalizeError) && (
        <p className="text-sm text-red-400">{finalizeError ?? error}</p>
      )}

      {loaded && !manualMode && !supported && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
          Meeting capture works in Google Chrome on desktop — tab-audio sharing
          isn’t available in this browser.
        </p>
      )}

      {/* Nothing below may render before the meeting row lands: `source` is what
          decides between a capture session and a manual one, and guessing wrong
          would offer a manual session the Start recording button — prompting for
          the microphone and a tab share on the one flow that never records. */}
      {!loaded ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-400">
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading…
            </>
          ) : (
            "Couldn’t load this meeting."
          )}
        </div>
      ) : manualMode ? (
        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-2 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.8fr)] lg:grid-rows-1">
          <div className="min-h-0">
            {/* Only while the session is open. The summary is built from these
                notes, so an editable box on a finished session would let the
                stored notes drift away from the summary shown for them. */}
            {manualSessionOpen ? (
              <NotesEditor
                ref={notesEditorRef}
                initialValue={meeting?.user_notes ?? ""}
                onSave={saveNotes}
              />
            ) : (
              <div className="flex h-full flex-col gap-2 rounded-lg border border-slate-700/50 bg-slate-800/20 p-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Your notes
                </p>
                <p className="flex-1 overflow-y-auto whitespace-pre-wrap text-sm text-slate-300">
                  {meeting?.user_notes || "No notes were taken in this session."}
                </p>
                <Link
                  href={`/meetings/${id}`}
                  className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
                >
                  This session is finished — view the summary →
                </Link>
              </div>
            )}
          </div>
          <div className="min-h-0">
            {assistEnabled ? (
              <AssistSidebar
                items={cards}
                onDismiss={dismiss}
                onAsk={manualAssist.sendAsk}
                askPending={manualAssist.askPending}
                askError={manualAssist.askError}
                interviewMode={candidateInterviewAssist}
                standaloneMode
                onBeforeAsk={() => notesEditorRef.current?.flush()}
                onClose={() => router.push("/meetings")}
              />
            ) : (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
                Turn on Live assist in Settings to ask Felix questions here.
              </div>
            )}
          </div>
        </div>
      ) : !recording ? (
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
          {/* This is the state a phone lands in when the laptop is already
              capturing — the capture controls above are useless to it, so point
              it at the read-only viewer. Fails closed with the assist flag: the
              viewer endpoint 404s without it. */}
          {assistEnabled && (
            <Link
              href={`/meetings/live/${id}/viewer`}
              className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
            >
              Following along on another device? Open the live assist view →
            </Link>
          )}
        </div>
      ) : (
        /* Live: transcript + notes side by side, with the assist panel as a
           third column on lg (overlay drawer below lg) when opened. */
        <div
          className={`grid min-h-0 flex-1 grid-cols-1 gap-4 ${
            assistOpen && assistEnabled
              ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_20rem]"
              : "lg:grid-cols-2"
          }`}
        >
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
          {assistOpen && assistEnabled && (
            <>
              {/* Desktop: third grid column */}
              <div className="hidden min-h-0 lg:block">
                <AssistSidebar
                  items={cards}
                  onDismiss={dismiss}
                  onAsk={sendAsk}
                  askPending={askPending}
                  askError={askError}
                  interviewMode={candidateInterviewAssist}
                  onClose={() => setAssistOpen(false)}
                />
              </div>
              {/* Below lg: right-side overlay drawer */}
              <div className="fixed inset-y-0 right-0 z-50 w-80 max-w-full p-3 lg:hidden">
                <AssistSidebar
                  items={cards}
                  onDismiss={dismiss}
                  onAsk={sendAsk}
                  askPending={askPending}
                  askError={askError}
                  interviewMode={candidateInterviewAssist}
                  onClose={() => setAssistOpen(false)}
                />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
