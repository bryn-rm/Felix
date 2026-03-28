"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import {
  Play,
  Pause,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  BookOpen,
  Loader2,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Briefing, Settings } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatGeneratedAt(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString("en", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  if (isToday) return `This morning at ${time}`;
  return d.toLocaleDateString("en", {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

function formatHistoryDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en", {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

function firstSentence(text: string): string {
  const m = text.match(/^.+?[.!?](?:\s|$)/);
  return m ? m[0].trim() : text.slice(0, 120) + (text.length > 120 ? "…" : "");
}

function formatDuration(secs: number): string {
  if (!isFinite(secs) || secs < 0) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const GENERATING_MESSAGES = [
  "Generating your briefing…",
  "Analysing your emails…",
  "Reviewing your calendar…",
  "Identifying follow-ups…",
  "Almost ready…",
];

// ---------------------------------------------------------------------------
// Audio player
// ---------------------------------------------------------------------------

function AudioPlayer({
  src,
  briefingId,
}: {
  src: string;
  briefingId: string;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const listenedCalledRef = useRef(false);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onDurationChange = () => setDuration(audio.duration || 0);
    const onEnded = () => setIsPlaying(false);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("loadedmetadata", onDurationChange);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);

    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("loadedmetadata", onDurationChange);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
    };
  }, []);

  function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.play();
      if (!listenedCalledRef.current) {
        listenedCalledRef.current = true;
        api.post(`/briefing/${briefingId}/listened`).catch(() => {});
      }
    } else {
      audio.pause();
    }
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    if (!audioRef.current || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audioRef.current.currentTime = pct * duration;
  }

  const progressPct = duration > 0 ? (currentTime / duration) * 100 : 0;
  const remaining = duration > 0 ? duration - currentTime : 0;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-slate-600/50 bg-slate-900/50 px-3 py-2.5">
      <audio ref={audioRef} src={src} preload="metadata" />

      {/* Play/pause */}
      <button
        onClick={togglePlay}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white transition-colors hover:bg-indigo-500"
        aria-label={isPlaying ? "Pause" : "Play"}
      >
        {isPlaying ? (
          <Pause className="h-3.5 w-3.5" />
        ) : (
          <Play className="h-3.5 w-3.5 translate-x-0.5" />
        )}
      </button>

      {/* Progress bar */}
      <div
        className="relative h-2 flex-1 cursor-pointer rounded-full bg-slate-700"
        onClick={seek}
        role="slider"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progressPct)}
      >
        <div
          className="absolute left-0 top-0 h-full rounded-full bg-indigo-500 transition-all"
          style={{ width: `${progressPct}%` }}
        />
        {/* Scrubber thumb */}
        <div
          className="absolute top-1/2 h-3 w-3 -translate-y-1/2 rounded-full bg-indigo-400 shadow"
          style={{ left: `calc(${progressPct}% - 6px)` }}
        />
      </div>

      {/* Time remaining */}
      <span className="w-10 shrink-0 text-right text-xs tabular-nums text-slate-400">
        -{formatDuration(remaining)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// History accordion item
// ---------------------------------------------------------------------------

function HistoryItem({ briefing }: { briefing: Briefing }) {
  const [open, setOpen] = useState(false);
  const preview = firstSentence(briefing.text);

  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-800/40">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-200">
            {formatHistoryDate(briefing.date)}
          </p>
          {!open && (
            <p className="mt-0.5 truncate text-xs text-slate-500">{preview}</p>
          )}
        </div>
        <span className="shrink-0 text-slate-500">
          {open ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </span>
      </button>

      {open && (
        <div className="border-t border-slate-700/50 px-4 py-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
            {briefing.text}
          </p>
          {briefing.audio_url && (
            <div className="mt-3">
              <AudioPlayer src={briefing.audio_url} briefingId={briefing.id} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BriefingPage() {
  // ---- Data ----
  const {
    data: todayBriefing,
    isLoading: loadingToday,
    mutate: mutateToday,
  } = useSWR<Briefing | null>("/briefing/today", (url: string) =>
    api.get<Briefing | null>(url),
  );

  const { data: history, isLoading: loadingHistory, mutate: mutateHistory } =
    useSWR<Briefing[]>("/briefing/history", (url: string) =>
      api.get<Briefing[]>(url),
    );

  const { data: settings } = useSWR<Settings>("/settings", (url: string) =>
    api.get<Settings>(url),
  );

  // ---- Generate ----
  const [generating, setGenerating] = useState(false);
  const [genMessage, setGenMessage] = useState(GENERATING_MESSAGES[0]);
  const [genError, setGenError] = useState<string | null>(null);
  const genIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const genMsgIdxRef = useRef(0);

  function startMessageCycle() {
    genMsgIdxRef.current = 0;
    setGenMessage(GENERATING_MESSAGES[0]);
    genIntervalRef.current = setInterval(() => {
      genMsgIdxRef.current =
        (genMsgIdxRef.current + 1) % GENERATING_MESSAGES.length;
      setGenMessage(GENERATING_MESSAGES[genMsgIdxRef.current]);
    }, 3_000);
  }

  function stopMessageCycle() {
    if (genIntervalRef.current) {
      clearInterval(genIntervalRef.current);
      genIntervalRef.current = null;
    }
  }

  async function handleGenerate() {
    setGenerating(true);
    setGenError(null);
    startMessageCycle();
    try {
      await api.post<Briefing>("/briefing/generate");
      await Promise.all([mutateToday(), mutateHistory()]);
    } catch (err) {
      setGenError(
        err instanceof ApiError ? err.message : "Generation failed. Try again.",
      );
    } finally {
      stopMessageCycle();
      setGenerating(false);
    }
  }

  // Cleanup interval on unmount
  useEffect(() => () => stopMessageCycle(), []);

  // ---- Render ----

  const briefingTime = settings?.briefing_time ?? "your scheduled time";

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto p-6 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-indigo-400" />
          <h1 className="text-xl font-semibold text-slate-100">
            Morning Briefing
          </h1>
        </div>
      </div>

      {/* ---- Today's briefing card ---- */}
      {loadingToday && (
        <div className="space-y-3 rounded-xl border border-slate-700/50 bg-slate-800/40 p-5">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className={`h-3 animate-pulse rounded bg-slate-700 ${
                i === 1 ? "w-1/4" : i === 4 ? "w-1/2" : "w-full"
              }`}
            />
          ))}
        </div>
      )}

      {!loadingToday && !todayBriefing && (
        /* Empty state */
        <div className="flex flex-col items-center gap-4 rounded-xl border border-slate-700/50 bg-slate-800/40 p-8 text-center">
          <BookOpen className="h-10 w-10 text-slate-600" />
          <div>
            <p className="text-base font-medium text-slate-300">
              No briefing yet today
            </p>
            <p className="mt-1 text-sm text-slate-500">
              Your briefing will be ready at {briefingTime}.
            </p>
          </div>
          {genError && (
            <p className="text-sm text-red-400">{genError}</p>
          )}
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-60"
          >
            {generating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {genMessage}
              </>
            ) : (
              <>
                <RefreshCw className="h-4 w-4" />
                Generate now
              </>
            )}
          </button>
        </div>
      )}

      {!loadingToday && todayBriefing && (
        <div className="rounded-xl border border-indigo-500/30 bg-indigo-600/5 p-5 space-y-4">
          {/* Meta row */}
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-slate-500">
              {formatGeneratedAt(todayBriefing.generated_at)}
            </p>
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="flex items-center gap-1.5 rounded-md border border-slate-600 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-60"
            >
              {generating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {generating ? genMessage : "Regenerate"}
            </button>
          </div>

          {/* Briefing text */}
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
            {todayBriefing.text}
          </p>

          {/* Audio player */}
          {todayBriefing.audio_url && (
            <AudioPlayer
              src={todayBriefing.audio_url}
              briefingId={todayBriefing.id}
            />
          )}

          {genError && (
            <p className="text-xs text-red-400">{genError}</p>
          )}
        </div>
      )}

      {/* ---- Briefing history ---- */}
      {(loadingHistory || (history && history.length > 0)) && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-400">
            Previous briefings
          </h2>

          {loadingHistory && (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-14 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
                />
              ))}
            </div>
          )}

          {!loadingHistory && history && (
            <div className="space-y-2">
              {history.slice(0, 7).map((b) => (
                <HistoryItem key={b.id} briefing={b} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
