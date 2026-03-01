/**
 * VoiceOrb — animated circular button for Felix voice activation.
 *
 * Visual states:
 *   idle       → solid indigo, static
 *   connecting → pulsing indigo
 *   listening  → indigo + expanding ripple rings
 *   thinking   → violet + slow rotation gradient
 *   speaking   → faster pulse (Felix talking)
 *   error      → red
 */

"use client";

import type { VoiceState } from "@/hooks/useVoice";

interface VoiceOrbProps {
  state: VoiceState;
  onClick: () => void;
  /** Diameter in pixels. Default 72. */
  size?: number;
}

type StateConfig = {
  label: string;
  buttonClass: string;
  showRipple: boolean;
  showSpin: boolean;
};

const STATE: Record<VoiceState, StateConfig> = {
  idle: {
    label: "Talk to Felix",
    buttonClass:
      "bg-indigo-600 hover:bg-indigo-500 shadow-indigo-500/30 hover:shadow-indigo-400/50",
    showRipple: false,
    showSpin: false,
  },
  connecting: {
    label: "Connecting…",
    buttonClass: "bg-indigo-500 animate-pulse shadow-indigo-500/40 cursor-wait",
    showRipple: false,
    showSpin: false,
  },
  listening: {
    label: "Listening…",
    buttonClass:
      "bg-indigo-600 shadow-indigo-500/60 ring-4 ring-indigo-400/40 ring-offset-2 ring-offset-black",
    showRipple: true,
    showSpin: false,
  },
  thinking: {
    label: "Thinking…",
    buttonClass:
      "bg-violet-600 shadow-violet-500/50 cursor-wait",
    showRipple: false,
    showSpin: true,
  },
  speaking: {
    label: "Speaking…",
    buttonClass:
      "bg-indigo-500 animate-pulse shadow-indigo-400/60",
    showRipple: false,
    showSpin: false,
  },
  error: {
    label: "Error — tap to retry",
    buttonClass:
      "bg-red-600 hover:bg-red-500 shadow-red-500/40",
    showRipple: false,
    showSpin: false,
  },
};

export function VoiceOrb({ state, onClick, size = 72 }: VoiceOrbProps) {
  const { label, buttonClass, showRipple, showSpin } = STATE[state];

  return (
    <div className="flex flex-col items-center gap-3 select-none">
      <button
        onClick={onClick}
        aria-label={label}
        title={label}
        style={{ width: size, height: size }}
        className={[
          "relative rounded-full shadow-lg transition-all duration-300",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70",
          buttonClass,
        ].join(" ")}
      >
        {/* Spinning gradient overlay for thinking state */}
        {showSpin && (
          <span
            className="absolute inset-0 rounded-full animate-spin"
            style={{
              background:
                "conic-gradient(from 0deg, transparent 0%, #7c3aed 50%, transparent 100%)",
              opacity: 0.4,
            }}
          />
        )}

        {/* Microphone icon */}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="w-7 h-7 text-white absolute inset-0 m-auto"
          aria-hidden
        >
          <path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3Z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3M8 22h8" />
        </svg>

        {/* Listening ripple rings */}
        {showRipple && (
          <>
            <span className="absolute inset-0 rounded-full animate-ping bg-indigo-400/25" />
            <span
              className="absolute rounded-full animate-ping bg-indigo-400/12"
              style={{ inset: "-8px", animationDelay: "0.3s" }}
            />
          </>
        )}
      </button>

      <span className="text-xs font-medium tracking-wide text-zinc-400">
        {label}
      </span>
    </div>
  );
}
