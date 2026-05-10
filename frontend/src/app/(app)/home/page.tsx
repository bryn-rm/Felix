"use client";

/**
 * /home — conversational AI-first home page.
 *
 * Layout (single column, max-w-2xl, full viewport):
 *   1. Top status bar      — greeting + quick counts
 *   2. Daily briefing card — premium card with audio playback + states
 *   3. Chat interface      — scrollable messages + pinned input bar
 *
 * Voice context is used for the chat mic so the same WebSocket session is
 * shared with the floating orb / VoiceModal.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Mic,
  Send,
  Play,
  Pause,
  Volume2,
  Loader2,
  Sparkles,
  Clock,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  X,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { Briefing, MeetingPrep, Settings } from "@/lib/types";
import { useVoiceContext } from "@/components/felix/VoiceContext";
import { useNextMeetingPrep } from "@/hooks/useMeetingPrep";

// ---------------------------------------------------------------------------
// Types & helpers
// ---------------------------------------------------------------------------

interface ChatMessage {
  id: string;
  role: "user" | "felix";
  text: string;
}

interface CountsResponse {
  action_required: number;
  overdue_followups: number;
}

interface CalendarTodayResponse {
  events: Array<{ id: string }>;
}

function timeOfDayGreeting(date = new Date()): "morning" | "afternoon" | "evening" {
  const h = date.getHours();
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}

function firstSentence(text: string, max = 80): string {
  const trimmed = text.trim();
  const m = trimmed.match(/^.+?[.!?](?:\s|$)/);
  const s = m ? m[0].trim() : trimmed;
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 60_000;
const MAX_MESSAGES = 20;

const SUGGESTED_PROMPTS = [
  "Read priority emails",
  "What's on today?",
  "Who's waiting on me?",
  "Any overdue follow-ups?",
];

// ---------------------------------------------------------------------------
// Skeleton primitives — matched to real layout shapes
// ---------------------------------------------------------------------------

function Shimmer({ className = "" }: { className?: string }) {
  return (
    <div
      className={`relative overflow-hidden rounded bg-slate-800/60 ${className}`}
    >
      <div className="absolute inset-0 -translate-x-full animate-[shimmer_1.6s_infinite] bg-gradient-to-r from-transparent via-white/[0.04] to-transparent" />
    </div>
  );
}

function FullPageSkeleton() {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col gap-6 px-4 py-6">
      {/* Status bar */}
      <Shimmer className="h-4 w-3/4" />

      {/* Briefing card */}
      <div className="space-y-3 rounded-2xl border border-white/[0.04] bg-[#0d1526] p-5">
        <div className="flex items-center gap-2">
          <Shimmer className="h-5 w-5 rounded-full" />
          <Shimmer className="h-3 w-32" />
        </div>
        <Shimmer className="h-3 w-full" />
        <Shimmer className="h-9 w-full rounded-lg" />
      </div>

      {/* Chat area */}
      <div className="flex-1 space-y-4">
        <Shimmer className="h-12 w-2/3" />
        <Shimmer className="h-12 w-1/2 self-end" />
      </div>

      {/* Input bar */}
      <Shimmer className="h-12 w-full rounded-xl" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Briefing card
// ---------------------------------------------------------------------------

function BriefingCard({
  briefing,
  onMutate,
}: {
  briefing: Briefing | null;
  onMutate: () => Promise<void>;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [pollMessage, setPollMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollDeadlineRef = useRef<number>(0);
  const autoPlayRef = useRef(false);

  function clearPoll() {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  useEffect(() => () => clearPoll(), []);

  // When audio_url first appears, optionally auto-play
  useEffect(() => {
    if (briefing?.audio_url && autoPlayRef.current && audioRef.current) {
      autoPlayRef.current = false;
      audioRef.current.play().catch(() => {});
    }
  }, [briefing?.audio_url]);

  function startPolling() {
    pollDeadlineRef.current = Date.now() + POLL_TIMEOUT_MS;
    setPollMessage("Generating audio…");
    pollTimerRef.current = setInterval(async () => {
      if (Date.now() > pollDeadlineRef.current) {
        clearPoll();
        setPollMessage("Taking longer than expected — try refreshing.");
        return;
      }
      try {
        await onMutate();
      } catch {
        // ignore — keep polling until deadline
      }
    }, POLL_INTERVAL_MS);
  }

  // Stop polling once audio_url shows up
  useEffect(() => {
    if (briefing?.audio_url && pollTimerRef.current) {
      clearPoll();
      setPollMessage(null);
    }
  }, [briefing?.audio_url]);

  async function handleGenerate() {
    setError(null);
    setGenerating(true);
    autoPlayRef.current = true;
    try {
      await api.post<Briefing>("/briefing/generate");
      await onMutate();
      // If we got back a briefing without audio yet, start polling
      startPolling();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't generate briefing. Try again.",
      );
    } finally {
      setGenerating(false);
    }
  }

  async function handleGenerateAudio() {
    setError(null);
    setGenerating(true);
    autoPlayRef.current = true;
    try {
      await api.post<Briefing>("/briefing/generate");
      await onMutate();
      startPolling();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't generate audio. Try again.",
      );
    } finally {
      setGenerating(false);
    }
  }

  function togglePlay() {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play();
    else a.pause();
  }

  function formatDur(secs: number): string {
    if (!isFinite(secs) || secs <= 0) return "";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  // ── State 3 — no briefing today ──
  if (!briefing) {
    return (
      <div className="rounded-2xl border border-indigo-500/20 bg-gradient-to-br from-[#0d1526] to-[#0d1526]/60 p-5 shadow-[0_0_24px_rgba(99,102,241,0.06)]">
        <div className="flex items-center gap-2 text-indigo-300">
          <Sparkles className="h-4 w-4" />
          <span className="text-xs font-semibold uppercase tracking-wider">
            Today&apos;s Briefing
          </span>
        </div>
        <p className="mt-2 text-sm text-slate-400">
          You don&apos;t have a briefing for today yet.
        </p>
        <button
          onClick={handleGenerate}
          disabled={generating}
          className="mt-4 flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-60"
        >
          {generating ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Generating your briefing…
            </>
          ) : (
            <>
              <Sparkles className="h-3.5 w-3.5" />
              Generate now
            </>
          )}
        </button>
        {pollMessage && (
          <p className="mt-2 text-xs text-slate-500">{pollMessage}</p>
        )}
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      </div>
    );
  }

  // ── State 1 — briefing with audio ──
  if (briefing.audio_url) {
    return (
      <div className="rounded-2xl border border-indigo-500/25 bg-gradient-to-br from-[#0d1526] to-[#0a1120] p-4 shadow-[0_0_30px_rgba(99,102,241,0.08)]">
        <audio
          ref={audioRef}
          src={briefing.audio_url}
          preload="metadata"
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onEnded={() => setIsPlaying(false)}
        />
        <div className="flex items-center gap-3">
          <button
            onClick={togglePlay}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg shadow-indigo-500/30 transition-colors hover:bg-indigo-500"
            aria-label={isPlaying ? "Pause briefing" : "Play briefing"}
          >
            {isPlaying ? (
              <Pause className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4 translate-x-0.5" />
            )}
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-indigo-300">
              <Volume2 className="h-3.5 w-3.5" />
              <span className="text-[11px] font-semibold uppercase tracking-wider">
                Today&apos;s Briefing
              </span>
              {duration > 0 && (
                <span className="text-[11px] text-slate-500">
                  · {formatDur(duration)}
                </span>
              )}
            </div>
            <p className="mt-1 truncate text-sm text-slate-300">
              {firstSentence(briefing.text, 80)}
            </p>
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <Link
            href="/briefing"
            className="text-xs text-indigo-400 hover:text-indigo-300"
          >
            Read full briefing →
          </Link>
        </div>
      </div>
    );
  }

  // ── State 2 — briefing exists but no audio yet ──
  return (
    <div className="rounded-2xl border border-indigo-500/20 bg-[#0d1526] p-5 shadow-[0_0_24px_rgba(99,102,241,0.06)]">
      <div className="flex items-center gap-2 text-indigo-300">
        <Sparkles className="h-4 w-4" />
        <span className="text-xs font-semibold uppercase tracking-wider">
          Today&apos;s Briefing
        </span>
      </div>
      <p className="mt-2 line-clamp-3 text-sm text-slate-300">
        {briefing.text}
      </p>
      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={handleGenerateAudio}
          disabled={generating || pollTimerRef.current !== null}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-60"
        >
          {generating || pollTimerRef.current !== null ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {pollMessage ?? "Generating audio…"}
            </>
          ) : (
            <>
              <Volume2 className="h-3.5 w-3.5" />
              Generate audio
            </>
          )}
        </button>
        <Link
          href="/briefing"
          className="text-xs text-indigo-400 hover:text-indigo-300"
        >
          Read full briefing →
        </Link>
      </div>
      {pollMessage && pollTimerRef.current === null && (
        <p className="mt-2 text-xs text-slate-500">{pollMessage}</p>
      )}
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Next-up meeting prep card
// ---------------------------------------------------------------------------

function formatStart(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (sameDay) {
    const mins = Math.round((d.getTime() - now.getTime()) / 60_000);
    if (mins > 0 && mins < 60) return `in ${mins} min · ${time}`;
    return time;
  }
  return d.toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Renders prep HTML through DOMPurify. The HTML is model-generated and may
 * include quoted email content, so we whitelist the same tag set the prompt
 * asks the model to emit (h3/p/ul/li/strong/em). DOMPurify is dynamically
 * imported because it is browser-only — same pattern as components/email/
 * EmailDetail.tsx SafeHtmlBody.
 */
function SafePrepBody({ html, fallbackText }: { html: string; fallbackText: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    import("dompurify")
      .then(({ default: DOMPurify }) => {
        const clean = DOMPurify.sanitize(html, {
          ALLOWED_TAGS: ["h3", "p", "ul", "li", "strong", "em"],
          ALLOWED_ATTR: [],
        });
        if (ref.current) {
          ref.current.innerHTML = clean;
        }
      })
      .catch(() => {
        if (ref.current) {
          ref.current.textContent = fallbackText || html;
        }
      });
  }, [html, fallbackText]);

  return (
    <div
      ref={ref}
      className="prose prose-invert prose-sm mt-3 max-w-none text-slate-300 [&_h3]:mt-3 [&_h3]:text-xs [&_h3]:font-semibold [&_h3]:uppercase [&_h3]:tracking-wider [&_h3]:text-amber-300/80 [&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:pl-5 [&_li]:my-0.5"
    />
  );
}

const PREP_DISMISSED_KEY = "felix.nextPrep.dismissed";
const PREP_MINIMIZED_KEY = "felix.nextPrep.minimized";

function readEventIdSet(key: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function writeEventIdSet(key: string, set: Set<string>) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(Array.from(set)));
  } catch {
    /* ignore quota / privacy-mode errors */
  }
}

function useNextPrepCardState(eventId: string | undefined) {
  const [dismissed, setDismissed] = useState<Set<string>>(() => readEventIdSet(PREP_DISMISSED_KEY));
  const [minimized, setMinimized] = useState<Set<string>>(() => readEventIdSet(PREP_MINIMIZED_KEY));

  const isDismissed = !!eventId && dismissed.has(eventId);
  const isMinimized = !!eventId && minimized.has(eventId);

  const dismiss = useCallback(() => {
    if (!eventId) return;
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(eventId);
      writeEventIdSet(PREP_DISMISSED_KEY, next);
      return next;
    });
  }, [eventId]);

  const toggleMinimize = useCallback(() => {
    if (!eventId) return;
    setMinimized((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      writeEventIdSet(PREP_MINIMIZED_KEY, next);
      return next;
    });
  }, [eventId]);

  return { isDismissed, isMinimized, dismiss, toggleMinimize };
}

function NextUpCard({
  prep,
  minimized,
  onToggleMinimize,
  onDismiss,
}: {
  prep: MeetingPrep;
  minimized: boolean;
  onToggleMinimize: () => void;
  onDismiss: () => void;
}) {
  const when = formatStart(prep.event_start);
  const title = prep.event_title || "Upcoming meeting";
  const hasContent = !prep.pending && !!prep.html;

  return (
    <div className="rounded-2xl border border-amber-500/20 bg-gradient-to-br from-[#1a1410] to-[#0d1526] p-5 shadow-[0_0_24px_rgba(245,158,11,0.06)]">
      <div className="flex items-center gap-2 text-amber-300">
        <Clock className="h-4 w-4" />
        <span className="text-[11px] font-semibold uppercase tracking-wider">
          Next up
        </span>
        {when && (
          <span className="text-[11px] text-slate-500">· {when}</span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleMinimize}
            aria-label={minimized ? "Expand prep card" : "Minimize prep card"}
            aria-expanded={!minimized}
            className="rounded p-1 text-slate-500 transition hover:bg-amber-500/10 hover:text-amber-300"
          >
            {minimized ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronUp className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss prep card"
            className="rounded p-1 text-slate-500 transition hover:bg-amber-500/10 hover:text-amber-300"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <p className="mt-2 text-sm font-medium text-slate-100">{title}</p>

      {!minimized && (hasContent ? (
        <SafePrepBody html={prep.html} fallbackText={prep.text} />
      ) : (
        <p className="mt-2 text-xs text-slate-500">
          Felix will assemble a prep card when the meeting gets closer.
        </p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

function StatusBar({
  name,
  counts,
  meetingsToday,
}: {
  name: string;
  counts: CountsResponse | null;
  meetingsToday: number | null;
}) {
  const greeting = `Good ${timeOfDayGreeting()}${name ? ` ${name}` : ""}`;

  const segments: string[] = [greeting];
  if (counts && counts.action_required > 0) {
    segments.push(
      `${counts.action_required} priority email${counts.action_required === 1 ? "" : "s"}`,
    );
  }
  if (meetingsToday !== null && meetingsToday > 0) {
    segments.push(
      `${meetingsToday} meeting${meetingsToday === 1 ? "" : "s"} today`,
    );
  }
  if (counts && counts.overdue_followups > 0) {
    segments.push(
      `${counts.overdue_followups} overdue follow-up${counts.overdue_followups === 1 ? "" : "s"}`,
    );
  }

  return (
    <p className="text-sm text-slate-400">
      {segments.join(" · ")}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function HomePage() {
  // Initial loading
  const [bootLoading, setBootLoading] = useState(true);

  // Status bar data
  const [counts, setCounts] = useState<CountsResponse | null>(null);
  const [meetingsToday, setMeetingsToday] = useState<number | null>(null);
  const [displayName, setDisplayName] = useState<string>("");

  // Briefing data
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [briefingLoaded, setBriefingLoaded] = useState(false);

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [hasUserSent, setHasUserSent] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Next-up meeting prep — surfaces a card when a meeting is approaching
  const { prep: nextPrep } = useNextMeetingPrep();
  const { isDismissed: prepDismissed, isMinimized: prepMinimized, dismiss: dismissPrep, toggleMinimize: toggleMinimizePrep } =
    useNextPrepCardState(nextPrep?.event_id);

  // Voice context — used for the chat mic
  const voice = useVoiceContext();
  const micActiveRef = useRef(false);

  const refreshBriefing = useCallback(async () => {
    try {
      const res = await api.get<{ briefing: Briefing | null }>(
        "/briefing/today",
      );
      setBriefing(res.briefing);
    } catch {
      setBriefing(null);
    } finally {
      setBriefingLoaded(true);
    }
  }, []);

  // Boot — fetch settings + counts + calendar in parallel
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      const settled = await Promise.allSettled([
        api.get<Settings>("/settings"),
        api.get<CountsResponse>("/emails/counts"),
        api.get<CalendarTodayResponse>("/calendar/today"),
        api.get<{ briefing: Briefing | null }>("/briefing/today"),
      ]);
      if (cancelled) return;

      if (settled[0].status === "fulfilled") {
        setDisplayName(
          (settled[0].value.display_name ?? "").split(" ")[0] || "",
        );
      }
      if (settled[1].status === "fulfilled") {
        setCounts(settled[1].value);
      }
      if (settled[2].status === "fulfilled") {
        setMeetingsToday(settled[2].value.events?.length ?? 0);
      }
      if (settled[3].status === "fulfilled") {
        setBriefing(settled[3].value.briefing);
      }
      setBriefingLoaded(true);
      setBootLoading(false);
    }
    boot();
    return () => {
      cancelled = true;
    };
  }, []);

  // Opening Felix message
  const openingMessage = useMemo<string>(() => {
    if (!briefingLoaded) return "";
    if (briefing) {
      return "Morning. Your briefing is ready above. What would you like to tackle first?";
    }
    if (counts && counts.action_required === 0 && (counts.overdue_followups ?? 0) === 0) {
      return "Hi — I'm Felix. I'm connecting to your inbox now. While we wait, try asking me something.";
    }
    return "Morning. I'm syncing your inbox. Ask me anything while you wait.";
  }, [briefingLoaded, briefing, counts]);

  // Auto-scroll chat to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, openingMessage, sending]);

  const submitMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      setHasUserSent(true);
      setInput("");
      setSending(true);

      // Optimistic user message
      const userMsg: ChatMessage = {
        id: uid(),
        role: "user",
        text: trimmed,
      };
      setMessages((prev) => [...prev, userMsg].slice(-MAX_MESSAGES));

      try {
        // Send the last 10 turns so the agent can see its own prior proposals
        // (e.g. a proposed calendar event awaiting "yes, book it").
        const history = messages.slice(-10).map((m) => ({
          role: m.role === "felix" ? "assistant" : "user",
          content: m.text,
        }));
        const res = await api.post<{ response: string; intent: string }>(
          "/voice/chat",
          { message: trimmed, history },
        );
        setMessages((prev) =>
          [
            ...prev,
            { id: uid(), role: "felix" as const, text: res.response },
          ].slice(-MAX_MESSAGES),
        );
      } catch (err) {
        setMessages((prev) =>
          [
            ...prev,
            {
              id: uid(),
              role: "felix" as const,
              text:
                err instanceof ApiError
                  ? `Sorry — ${err.message}`
                  : "Sorry — I couldn't reach the server.",
            },
          ].slice(-MAX_MESSAGES),
        );
      } finally {
        setSending(false);
      }
    },
    [sending, messages],
  );

  // ── Mic — wire interim transcript into the input field, auto-submit on final ──
  useEffect(() => {
    if (!micActiveRef.current) return;
    if (voice.interimTranscript) {
      setInput(voice.interimTranscript);
    }
  }, [voice.interimTranscript]);

  useEffect(() => {
    if (!micActiveRef.current) return;
    // When the WebSocket loop finalises the user message, useVoice pushes a
    // role==="user" message into voice.messages. Detect that and submit.
    const last = voice.messages[voice.messages.length - 1];
    if (last && last.role === "user") {
      micActiveRef.current = false;
      voice.stop();
      submitMessage(last.text);
    }
  }, [voice.messages, voice, submitMessage]);

  function handleMicClick() {
    if (micActiveRef.current) {
      micActiveRef.current = false;
      voice.stop();
      return;
    }
    micActiveRef.current = true;
    voice.start();
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    submitMessage(input);
  }

  if (bootLoading) {
    return <FullPageSkeleton />;
  }

  const showChips = !hasUserSent;
  const micListening =
    micActiveRef.current && voice.state === "listening";

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col px-4 py-6">
      {/* Section A — status bar */}
      <StatusBar
        name={displayName}
        counts={counts}
        meetingsToday={meetingsToday}
      />

      {/* Section B — briefing card */}
      <div className="mt-5">
        <BriefingCard briefing={briefing} onMutate={refreshBriefing} />
      </div>

      {/* Section B.5 — next-up meeting prep (only when one is on the horizon) */}
      {nextPrep && !prepDismissed && (
        <div className="mt-3">
          <NextUpCard
            prep={nextPrep}
            minimized={prepMinimized}
            onToggleMinimize={toggleMinimizePrep}
            onDismiss={dismissPrep}
          />
        </div>
      )}

      {/* Section C — chat */}
      <div className="mt-6 flex min-h-0 flex-1 flex-col">
        {/* Messages */}
        <div className="flex-1 space-y-4 overflow-y-auto pb-4 pr-1">
          {/* Felix opening message */}
          {openingMessage && (
            <FelixBubble text={openingMessage} />
          )}

          {messages.map((m) =>
            m.role === "felix" ? (
              <FelixBubble key={m.id} text={m.text} />
            ) : (
              <UserBubble key={m.id} text={m.text} />
            ),
          )}

          {sending && <TypingIndicator />}

          <div ref={messagesEndRef} />
        </div>

        {/* Suggested chips */}
        {showChips && (
          <div className="mb-3 flex flex-wrap gap-2">
            {SUGGESTED_PROMPTS.map((p) => (
              <button
                key={p}
                onClick={() => submitMessage(p)}
                disabled={sending}
                className="rounded-full border border-white/[0.06] bg-[#0d1526] px-3 py-1.5 text-xs text-slate-300 transition-colors hover:border-indigo-500/40 hover:text-slate-100 disabled:opacity-50"
              >
                {p}
              </button>
            ))}
          </div>
        )}

        {/* Input bar */}
        <form
          onSubmit={handleSubmit}
          className="flex items-center gap-2 rounded-xl border border-white/[0.06] bg-[#0d1526] p-2 shadow-[0_0_24px_rgba(0,0,0,0.3)]"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask Felix anything…"
            className="flex-1 bg-transparent px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none"
            disabled={sending}
          />
          <button
            type="button"
            onClick={handleMicClick}
            className={[
              "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
              micListening
                ? "bg-indigo-600 text-white"
                : "text-slate-400 hover:bg-slate-800 hover:text-slate-100",
            ].join(" ")}
            aria-label={micListening ? "Stop dictation" : "Start dictation"}
          >
            <Mic className="h-4 w-4" />
          </button>
          <button
            type="submit"
            disabled={sending || input.trim().length === 0}
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Send"
          >
            {sending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat bubbles
// ---------------------------------------------------------------------------

function FelixBubble({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-600/30 text-[10px] font-bold text-indigo-300">
        F
      </div>
      <div className="max-w-[80%] rounded-2xl rounded-tl-sm border border-white/[0.04] bg-[#0d1526] px-4 py-2.5 text-sm leading-relaxed text-slate-200">
        {text}
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex items-start justify-end gap-2">
      <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-indigo-600 px-4 py-2.5 text-sm leading-relaxed text-white">
        {text}
      </div>
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-[10px] font-bold text-white">
        Y
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-600/30 text-[10px] font-bold text-indigo-300">
        F
      </div>
      <div className="rounded-2xl rounded-tl-sm border border-white/[0.04] bg-[#0d1526] px-4 py-3">
        <div className="flex items-center gap-1">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-500 [animation-delay:-0.3s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-500 [animation-delay:-0.15s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-500" />
        </div>
      </div>
    </div>
  );
}
