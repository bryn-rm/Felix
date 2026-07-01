"use client";

import type { CaptureStatus } from "@/hooks/useMeetingCapture";

const LABELS: Record<CaptureStatus, string> = {
  idle: "Not recording",
  requesting: "Requesting permissions…",
  connecting: "Connecting…",
  recording: "Recording",
  reconnecting: "Reconnecting…",
  stopping: "Finishing up…",
  ended: "Stopped",
  error: "Error",
};

/** Pulsing dot + status label for the live capture page. */
export function RecordingIndicator({ status }: { status: CaptureStatus }) {
  const live = status === "recording";
  const transitional =
    status === "connecting" ||
    status === "requesting" ||
    status === "reconnecting" ||
    status === "stopping";

  const dot = live
    ? "bg-red-500"
    : transitional
      ? "bg-amber-400"
      : status === "error"
        ? "bg-red-500"
        : "bg-slate-500";

  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-300">
      <span className="relative flex h-2.5 w-2.5">
        {live && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
        )}
        <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${dot}`} />
      </span>
      {LABELS[status]}
    </span>
  );
}
