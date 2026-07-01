"use client";

import { useEffect, useRef } from "react";
import type { LiveLine } from "@/hooks/useMeetingCapture";

function speakerLabel(speaker: "me" | "them"): string {
  return speaker === "me" ? "You" : "Them";
}

/**
 * Live two-channel transcript. Finalized lines render as speaker-tagged bubbles
 * (You right / Them left); the current interim for each side shows faded
 * beneath, replaced in place as the model refines it.
 */
export function LiveTranscript({
  lines,
  interim,
}: {
  lines: LiveLine[];
  interim: { me: string; them: string };
}) {
  const endRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the newest line / interim.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines, interim]);

  const empty = lines.length === 0 && !interim.me && !interim.them;

  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto pr-1">
      {empty && (
        <p className="m-auto max-w-sm text-center text-sm text-slate-500">
          Transcript will appear here as people speak. Keep the shared tab
          playing audio and your mic unmuted.
        </p>
      )}

      {lines.map((line, i) => (
        <Bubble key={`${line.ts_start}-${i}`} speaker={line.speaker} text={line.text} />
      ))}

      {interim.them && <Bubble speaker="them" text={interim.them} faded />}
      {interim.me && <Bubble speaker="me" text={interim.me} faded />}

      <div ref={endRef} />
    </div>
  );
}

function Bubble({
  speaker,
  text,
  faded = false,
}: {
  speaker: "me" | "them";
  text: string;
  faded?: boolean;
}) {
  const mine = speaker === "me";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={[
          "max-w-[80%] rounded-lg px-3 py-2 text-sm",
          mine
            ? "bg-indigo-600/20 text-slate-100"
            : "bg-slate-800/60 text-slate-200",
          faded ? "opacity-50" : "",
        ].join(" ")}
      >
        <p className="mb-0.5 text-[10px] uppercase tracking-wider text-slate-500">
          {speakerLabel(speaker)}
        </p>
        {text}
      </div>
    </div>
  );
}
