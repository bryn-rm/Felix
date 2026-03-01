/**
 * TranscriptDisplay — scrolling conversation history for VoiceModal.
 *
 * Shows the last 10 messages (≈5 exchanges):
 *   - User messages: right-aligned, indigo bubble
 *   - Felix messages: left-aligned, dark bubble
 *
 * Auto-scrolls to the bottom whenever messages change.
 */

"use client";

import { useEffect, useRef } from "react";

import type { VoiceMessage } from "@/hooks/useVoice";

interface TranscriptDisplayProps {
  messages: VoiceMessage[];
}

export function TranscriptDisplay({ messages }: TranscriptDisplayProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Keep the latest message in view
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Show only the last 10 messages (≈5 exchanges)
  const visible = messages.slice(-10);

  if (visible.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-2 text-zinc-700">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="w-8 h-8"
          aria-hidden
        >
          <path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3Z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3M8 22h8" />
        </svg>
        <p className="text-sm">Say something to get started…</p>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-3 overflow-y-auto max-h-72 pr-1 scroll-smooth"
      aria-live="polite"
      aria-label="Conversation history"
    >
      {visible.map((msg, i) => (
        <div
          key={i}
          className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          {/* Avatar dot for Felix */}
          {msg.role === "felix" && (
            <span className="mr-2 mt-2.5 w-1.5 h-1.5 rounded-full bg-indigo-400 shrink-0" />
          )}

          <div
            className={[
              "max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
              msg.role === "user"
                ? "bg-indigo-600 text-white rounded-br-sm"
                : "bg-zinc-800 text-zinc-100 rounded-bl-sm",
            ].join(" ")}
          >
            {msg.text}
          </div>
        </div>
      ))}

      {/* Invisible scroll anchor */}
      <div ref={bottomRef} />
    </div>
  );
}
