"use client";

import { useCallback, useEffect, useRef } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  PlugZap,
  Radio,
  Sparkles,
} from "lucide-react";

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

/** How close to the bottom still counts as "following along", in px. */
const FOLLOW_THRESHOLD_PX = 120;

/**
 * Live Assist on a second device — the phone surface.
 *
 * Interactive but never a capture client: this route imports no capture
 * machinery (`useMeetingCapture`, the meeting WebSocket, the notes editor), so
 * there is nothing here that could request the microphone or a tab share, open
 * the capture socket, start STT, or take watcher ownership. Asking goes over
 * REST to the same server-side ask implementation the capturing device uses,
 * which is what makes the two devices share one set of limits.
 *
 * Laid out for the way it is actually used: held in one hand, glanced at for a
 * second or two during a meeting someone else's laptop is recording. So the
 * meeting's identity and state stay pinned at the top, the ask box stays pinned
 * at the bottom within thumb reach, and only the card stream between them
 * scrolls.
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
  const manualMode = meeting?.source === "manual_notes";
  // Only ever false for a capture meeting whose socket has gone (null means the
  // question doesn't apply), so this can't fire on a manual session.
  const captureDropped = live && captureAttached === false;

  const listRef = useRef<HTMLDivElement | null>(null);
  const newestCardRef = useRef<HTMLDivElement | null>(null);
  const pendingRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerFocusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const programmaticScrollTopRef = useRef<number | null>(null);
  const lastSeenNewestIdRef = useRef<string | null>(null);
  // Whether the user is at the live edge. Cards arrive mid-meeting without
  // being asked for, so scrolling on every one would yank a card someone is
  // half-way through reading off the screen.
  const followingRef = useRef(true);

  const onListScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const programmaticTop = programmaticScrollTopRef.current;
    if (programmaticTop !== null) {
      programmaticScrollTopRef.current = null;
      // Assigning scrollTop fires the same event as a user's gesture. Ignore
      // only the event that landed at our requested position; a user gesture
      // that interrupts it must still turn following off normally.
      if (Math.abs(el.scrollTop - programmaticTop) < 1) return;
    }
    followingRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX;
  }, []);

  const scrollStreamTo = useCallback(
    (list: HTMLDivElement, target: HTMLDivElement) => {
      const top = Math.max(0, target.offsetTop - list.offsetTop);
      programmaticScrollTopRef.current = top;
      list.scrollTop = top;
    },
    [],
  );

  // Bring the TOP of the newest card into view, not the bottom of the list: an
  // interview answer can be several screens long on a phone, and scrolling to
  // the end of it would land the reader in the middle of an answer they hadn't
  // started. Measured rather than `scrollIntoView` so this scrolls the card
  // stream alone and never the page around it.
  const newestId = items.length ? items[items.length - 1].id : null;
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;

    // An explicit ask is direct user intent to see what happens next. Reveal
    // its pending row even if the reader had previously moved up the stream,
    // and keep following for the answer that replaces it.
    if (askPending) {
      const pending = pendingRef.current;
      if (!pending) return;
      followingRef.current = true;
      scrollStreamTo(list, pending);
      return;
    }

    // A card arriving by itself should not disturb someone reading an older
    // one. Mark it seen even when we do not scroll so a later unrelated render
    // cannot unexpectedly jump to it.
    if (!newestId || lastSeenNewestIdRef.current === newestId) return;
    lastSeenNewestIdRef.current = newestId;
    const newest = newestCardRef.current;
    if (!newest || !followingRef.current) return;
    scrollStreamTo(list, newest);
  }, [newestId, askPending, scrollStreamTo]);

  // The mobile keyboard covers the bottom of the viewport without resizing the
  // app's full-height shell, which would otherwise leave the user typing into a
  // box they cannot see. Focus is the moment to put it back in view.
  const onComposerFocus = useCallback(() => {
    // After the keyboard animation, or the scroll lands where the layout used
    // to be. Re-read the ref at that point rather than closing over the node,
    // so a viewer closed in between does nothing instead of scrolling a
    // detached element.
    if (composerFocusTimerRef.current !== null) {
      clearTimeout(composerFocusTimerRef.current);
    }
    composerFocusTimerRef.current = setTimeout(
      () => composerRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" }),
      300,
    );
  }, []);

  useEffect(
    () => () => {
      if (composerFocusTimerRef.current !== null) {
        clearTimeout(composerFocusTimerRef.current);
      }
    },
    [],
  );

  return (
    <div className="flex h-full flex-col gap-3 p-3 sm:gap-4 sm:p-6">
      <div className="flex items-start gap-2">
        <Link
          href="/meetings"
          className="-m-1 rounded p-2 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
          aria-label="Back to meetings"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-base font-semibold text-slate-100 sm:text-lg">
            {meeting?.title || "Untitled meeting"}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
            {/* Until the row lands, neither "live" nor "ended" is true, and
                claiming either would be a guess about the one thing this page
                is for. */}
            {meeting && (
              <span
                className={`flex items-center gap-1 text-xs ${
                  !live
                    ? "text-slate-400"
                    : manualMode
                      ? "text-emerald-300"
                    : captureDropped
                      ? "text-amber-300"
                      : "text-red-300"
                }`}
              >
                {!live ? (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                    Meeting ended
                  </>
                ) : manualMode ? (
                  <>
                    <FileText className="h-3.5 w-3.5 shrink-0" />
                    Live · manual notes · no recording
                  </>
                ) : captureDropped ? (
                  <>
                    <PlugZap className="h-3.5 w-3.5 shrink-0" />
                    Recording device disconnected
                  </>
                ) : (
                  <>
                    <Radio className="h-3.5 w-3.5 shrink-0 animate-pulse" />
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

      {/* Keep capture ownership visible even after cards fill the stream. */}
      <p className="rounded-lg border border-white/[0.04] bg-[#0d1526]/60 px-3 py-2 text-xs text-slate-400">
        {manualMode
          ? "Second screen. This session uses manual notes and isn’t recording."
          : "Second screen. You can ask Felix questions here; recording and notes stay on the device that started this meeting."}
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
          <div className="flex items-center gap-2 border-b border-white/[0.04] px-3 py-2.5">
            <Sparkles className="h-4 w-4 shrink-0 text-indigo-400" />
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Live assist
            </p>
          </div>

          <div
            ref={listRef}
            onScroll={onListScroll}
            className="min-h-0 flex-1 space-y-2.5 overflow-y-auto overscroll-contain p-3"
          >
            {meeting && !live && (
              <p className="rounded-lg border border-white/[0.04] bg-[#0d1526] p-3 text-xs leading-relaxed text-slate-400">
                This meeting has ended, so no new cards will appear and asking is
                closed.{" "}
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
              <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-200">
                The recording device isn’t connected right now, so no new cards
                will appear until it reconnects. You can still ask questions.
              </p>
            )}

            {items.length === 0 && !askPending ? (
              <p className="px-4 pt-8 text-center text-xs leading-relaxed text-slate-600">
                {live
                  ? manualMode
                    ? "Ask a question, or wait for a card to appear."
                    : "Felix is listening on the other device. Ask a question, or wait for a card to appear."
                  : "No assist cards were kept for this meeting."}
              </p>
            ) : (
              items.map((item, index) => (
                <div
                  key={item.id}
                  ref={index === items.length - 1 ? newestCardRef : undefined}
                >
                  <AssistCard
                    item={item}
                    onDismiss={dismiss}
                    // Expansions are asks like any other, so they work here for
                    // the same reason typed questions do — while the meeting is
                    // open. On an ended one the server would refuse them.
                    onExpand={live ? sendAsk : undefined}
                    askPending={askPending}
                  />
                </div>
              ))
            )}

            {/* A pending ask belongs in the stream, not only above the ask box:
                that indicator is the first thing the keyboard covers, and this
                is where the answer is about to appear. */}
            {askPending && (
              <div
                ref={pendingRef}
                className="flex items-center gap-2 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] p-3 text-xs text-indigo-300"
              >
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                Felix is working on your question…
              </div>
            )}
          </div>

          {/* No ask box once the meeting is over: the session is closed to
              writes, so offering one would only produce a rejection. */}
          {live && (
            <div ref={composerRef} onFocusCapture={onComposerFocus}>
              <AssistComposer
                onAsk={sendAsk}
                askPending={askPending}
                askError={askError}
                interviewMode={usesCandidateInterviewAssist(meeting)}
                externalPendingIndicator
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
