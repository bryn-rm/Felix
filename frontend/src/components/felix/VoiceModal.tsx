/**
 * VoiceModal — full-screen overlay for Felix voice interactions.
 *
 * Shows:
 *   - TranscriptDisplay (scrolling conversation history)
 *   - Live interim transcript (greyed, streaming)
 *   - VoiceOrb (tap to start / stop)
 *   - Interrupt button (visible while Felix is speaking)
 *   - Escape key or × button closes the modal
 *
 * Props:
 *   token    — Supabase access token (string from useSession / getSession)
 *   onClose  — called when the user ends the session
 */

"use client";

import { useEffect } from "react";

import { useVoice } from "@/hooks/useVoice";
import { TranscriptDisplay } from "./TranscriptDisplay";
import { VoiceOrb } from "./VoiceOrb";

interface VoiceModalProps {
  token: string;
  onClose: () => void;
}

export function VoiceModal({ token, onClose }: VoiceModalProps) {
  const { state, interimTranscript, messages, error, start, stop, interrupt } =
    useVoice(token);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        stop();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [stop, onClose]);

  const handleOrbClick = () => {
    if (state === "idle" || state === "error") {
      start();
    } else {
      stop();
      onClose();
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
            onClose();
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

        {/* Live interim transcript */}
        {interimTranscript && (
          <p className="mt-3 px-1 text-sm italic text-zinc-500 leading-relaxed">
            {interimTranscript}
          </p>
        )}

        {/* Error message */}
        {error && (
          <p className="mt-2 px-1 text-sm text-red-400">
            {error}
          </p>
        )}
      </div>

      {/* ── Controls ── */}
      <div className="flex flex-col items-center gap-4 pb-2">
        {/* Interrupt button — only when Felix is speaking */}
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
              onClose();
            }}
            className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors"
          >
            End conversation
          </button>
        )}

        {/* Keyboard hint */}
        {!isActive && state === "idle" && (
          <p className="text-xs text-zinc-700">Press Esc to close</p>
        )}
      </div>
    </div>
  );
}
