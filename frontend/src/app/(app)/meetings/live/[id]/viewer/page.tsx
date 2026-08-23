"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, Loader2, Radio, Sparkles } from "lucide-react";

import { AssistCard } from "@/components/meetings/AssistCard";
import { assistModeLabel } from "@/components/meetings/constants";
import { useLiveAssistViewer } from "@/hooks/useLiveAssistViewer";

interface PageProps {
  params: { id: string };
}

/**
 * Live Assist viewer — the phone / second-screen surface.
 *
 * Read-only by construction, not by hidden controls. This route imports none of
 * the capture machinery: no `useMeetingCapture`, no meeting WebSocket, no notes
 * editor, no ask box. There is therefore nothing here that could request the
 * microphone or a tab share, open the capture socket, or start a second assist
 * generation loop — the device that started the meeting stays its only owner.
 *
 * All this page does is poll persisted assist state (`GET
 * /meetings/{id}/live-view`) and render it, stopping the moment the meeting
 * leaves `recording`.
 */
export default function LiveAssistViewerPage({ params }: PageProps) {
  const { id } = params;
  const { meeting, items, live, isLoading, error } = useLiveAssistViewer(id);
  const modeLabel = assistModeLabel(meeting);

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
                  live ? "text-red-300" : "text-slate-400"
                }`}
              >
                {live ? (
                  <>
                    <Radio className="h-3.5 w-3.5 animate-pulse" />
                    Live · recording on another device
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Meeting ended
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

      {/* Say plainly that this device is a viewer, so nobody waits here for a
          Stop button that lives on the capturing device. */}
      <p className="rounded-lg border border-white/[0.04] bg-[#0d1526]/60 px-3 py-2 text-xs text-slate-400">
        Viewing only. Recording, notes and questions stay on the device that
        started this meeting.
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
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pb-6">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-indigo-400" />
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Live assist
            </p>
          </div>

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

          {items.length === 0 ? (
            <p className="pt-6 text-center text-xs leading-relaxed text-slate-600">
              {live
                ? "Felix is listening on the other device. Cards appear here as they’re written."
                : "No assist cards were kept for this meeting."}
            </p>
          ) : (
            items.map((item) => <AssistCard key={item.id} item={item} />)
          )}
        </div>
      )}
    </div>
  );
}
