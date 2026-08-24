"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, Loader2, PlugZap, Radio, Sparkles } from "lucide-react";

import { AssistCard } from "@/components/meetings/AssistCard";
import { AssistComposer } from "@/components/meetings/AssistComposer";
import {
  assistModeLabel,
  usesCandidateInterviewAssist,
} from "@/components/meetings/constants";
import { useLiveAssistViewer } from "@/hooks/useLiveAssistViewer";

interface PageProps {
  params: { id: string };
}

/**
 * Live Assist on a second device — the phone surface.
 *
 * Interactive but never a capture client: this route imports no capture
 * machinery (`useMeetingCapture`, the meeting WebSocket, the notes editor), so
 * there is nothing here that could request the microphone or a tab share, open
 * the capture socket, start STT, or take watcher ownership. Asking goes over
 * REST to the same server-side ask implementation the capturing device uses,
 * which is what makes the two devices share one set of limits.
 */
export default function LiveAssistViewerPage({ params }: PageProps) {
  const { id } = params;
  const {
    meeting,
    items,
    live,
    captureAttached,
    isLoading,
    error,
    sendAsk,
    askPending,
    askError,
    dismiss,
  } = useLiveAssistViewer(id);
  const modeLabel = assistModeLabel(meeting);
  // Only ever false for a capture meeting whose socket has gone (null means the
  // question doesn't apply), so this can't fire on a manual session.
  const captureDropped = live && captureAttached === false;

  return (
    <div className="flex h-full flex-col gap-4 p-4 sm:p-6">
      <div className="flex items-start gap-3">
        <Link
          href="/meetings"
          className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
          aria-label="Back to meetings"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-slate-100">
            {meeting?.title || "Untitled meeting"}
          </h1>
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            {/* Until the row lands, neither "live" nor "ended" is true, and
                claiming either would be a guess about the one thing this page
                is for. */}
            {meeting && (
              <span
                className={`flex items-center gap-1 text-xs ${
                  !live
                    ? "text-slate-400"
                    : captureDropped
                      ? "text-amber-300"
                      : "text-red-300"
                }`}
              >
                {!live ? (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Meeting ended
                  </>
                ) : captureDropped ? (
                  <>
                    <PlugZap className="h-3.5 w-3.5" />
                    Recording device disconnected
                  </>
                ) : (
                  <>
                    <Radio className="h-3.5 w-3.5 animate-pulse" />
                    Live · recording on another device
                  </>
                )}
              </span>
            )}
            {modeLabel && (
              <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-xs text-indigo-300">
                {modeLabel}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Say plainly what this device is, so nobody waits here for a Stop
          button that lives on the capturing device. */}
      <p className="rounded-lg border border-white/[0.04] bg-[#0d1526]/60 px-3 py-2 text-xs text-slate-400">
        Second screen. You can ask Felix questions here; recording and notes stay
        on the device that started this meeting.
      </p>

      {error && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
          Live assist isn’t available for this meeting.
        </p>
      )}

      {!meeting && !error ? (
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
      ) : (
        <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-white/[0.04] bg-[#0d1526]/60">
          <div className="flex items-center gap-2 border-b border-white/[0.04] p-3">
            <Sparkles className="h-4 w-4 text-indigo-400" />
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Live assist
            </p>
          </div>

          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
            {meeting && !live && (
              <p className="rounded-lg border border-white/[0.04] bg-[#0d1526] p-3 text-xs text-slate-400">
                This meeting has ended, so no new cards will appear.{" "}
                <Link
                  href={`/meetings/${id}`}
                  className="font-medium text-indigo-400 hover:text-indigo-300"
                >
                  View the summary →
                </Link>
              </p>
            )}

            {captureDropped && (
              // Honest about what is and isn't affected: the socket carries
              // transcript, so proactive cards stop — asks go over REST and
              // keep working.
              <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                The recording device isn’t connected right now, so no new cards
                will appear until it reconnects. You can still ask questions.
              </p>
            )}

            {items.length === 0 ? (
              <p className="pt-6 text-center text-xs leading-relaxed text-slate-600">
                {live
                  ? "Felix is listening on the other device. Ask a question, or wait for a card to appear."
                  : "No assist cards were kept for this meeting."}
              </p>
            ) : (
              items.map((item) => (
                <AssistCard
                  key={item.id}
                  item={item}
                  onDismiss={dismiss}
                  // Expansions are asks like any other, so they work here for
                  // the same reason typed questions do — while the meeting is
                  // open. On an ended one the server would refuse them.
                  onExpand={live ? sendAsk : undefined}
                  askPending={askPending}
                />
              ))
            )}
          </div>

          {/* No ask box once the meeting is over: the session is closed to
              writes, so offering one would only produce a rejection. */}
          {live && (
            <AssistComposer
              onAsk={sendAsk}
              askPending={askPending}
              askError={askError}
              interviewMode={usesCandidateInterviewAssist(meeting)}
            />
          )}
        </div>
      )}
    </div>
  );
}
