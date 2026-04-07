/**
 * VoiceModal — full-screen overlay for Felix voice interactions.
 *
 * Reads its session state from VoiceContext so the orb FAB, the keyboard
 * shortcut and the modal all share one WebSocket session.
 */

"use client";

import { useEffect } from "react";

import { useVoiceContext } from "./VoiceContext";
import { TranscriptDisplay } from "./TranscriptDisplay";
import { VoiceOrb } from "./VoiceOrb";

export function VoiceModal() {
  const {
    state,
    interimTranscript,
    messages,
    error,
    start,
    stop,
    interrupt,
    closeModal,
  } = useVoiceContext();

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        stop();
        closeModal();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [stop, closeModal]);

  const handleOrbClick = () => {
    if (state === "idle" || state === "error") {
      start();
    } else {
      stop();
      closeModal();
    }
  };

  const isActive =
    state === "listening" || state === "thinking" || state === "speaking";

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-between bg-black/92 backdrop-blur-md p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Felix Voice Assistant"
    >
      {/* ── Header ── */}
      <div className="w-full flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
          <span className="text-sm font-semibold text-zinc-300 tracking-wide">
            Felix
          </span>
        </div>

        <button
          onClick={() => {
            stop();
            closeModal();
          }}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
          aria-label="Close voice assistant"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-5 h-5"
            aria-hidden
          >
            <path d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* ── Conversation area ── */}
      <div className="flex-1 w-full max-w-lg flex flex-col justify-end overflow-hidden py-4">
        <TranscriptDisplay messages={messages} />

        {interimTranscript && (
          <p className="mt-3 px-1 text-sm italic text-zinc-500 leading-relaxed">
            {interimTranscript}
          </p>
        )}

        {state === "error" && error && (
          <div className="mt-3 flex flex-col items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-4 text-center">
            <p className="text-sm text-red-400">{error}</p>
            <button
              onClick={() => start()}
              className={[
                "rounded-full border border-red-500/50 px-5 py-1.5 text-xs font-medium",
                "text-red-300 transition-colors hover:border-red-400 hover:text-red-200",
              ].join(" ")}
            >
              Retry
            </button>
          </div>
        )}

        {state !== "error" && error && (
          <p className="mt-2 px-1 text-sm text-red-400">{error}</p>
        )}
      </div>

      {/* ── Controls ── */}
      <div className="flex flex-col items-center gap-4 pb-2">
        {state === "speaking" && (
          <button
            onClick={interrupt}
            className={[
              "text-xs font-medium rounded-full px-5 py-1.5 transition-colors",
              "border border-zinc-600 text-zinc-400 hover:border-zinc-400 hover:text-white",
            ].join(" ")}
          >
            Interrupt Felix
          </button>
        )}

        <VoiceOrb state={state} onClick={handleOrbClick} size={80} />

        {isActive && (
          <button
            onClick={() => {
              stop();
              closeModal();
            }}
            className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors"
          >
            End conversation
          </button>
        )}

        {state === "idle" && (
          <p className="text-xs text-zinc-700">
            Tap orb to start · Esc to close
          </p>
        )}
        {state === "connecting" && (
          <p className="text-xs text-zinc-700">Connecting…</p>
        )}
      </div>
    </div>
  );
}
