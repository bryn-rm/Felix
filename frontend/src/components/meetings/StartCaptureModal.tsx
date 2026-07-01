"use client";

import { useState } from "react";
import { Loader2, X } from "lucide-react";

import type { MeetingTemplate } from "@/lib/types";
import { TEMPLATES } from "@/components/meetings/constants";

/**
 * Start-capture modal: template picker + title + a consent checkbox (UK GDPR —
 * the user confirms they'll inform participants). Audio is never stored; only
 * the transcript text is kept, which the consent copy states.
 */
export function StartCaptureModal({
  open,
  onClose,
  onStart,
}: {
  open: boolean;
  onClose: () => void;
  onStart: (template: MeetingTemplate, title: string) => Promise<void>;
}) {
  const [template, setTemplate] = useState<MeetingTemplate>("general");
  const [title, setTitle] = useState("");
  const [consent, setConsent] = useState(false);
  const [starting, setStarting] = useState(false);

  if (!open) return null;

  async function handleStart() {
    if (!consent || starting) return;
    setStarting(true);
    try {
      await onStart(template, title.trim());
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-xl border border-slate-700 bg-[#0d1526] p-6 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Start capture</h2>
            <p className="mt-1 text-sm text-slate-500">
              Felix transcribes both sides of an in-browser meeting and writes you
              an enhanced summary.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-5 space-y-4">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Meeting type
            </label>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {TEMPLATES.map((t) => {
                const active = template === t.value;
                return (
                  <button
                    key={t.value}
                    onClick={() => setTemplate(t.value)}
                    className={`rounded-lg border p-2.5 text-left transition-colors ${
                      active
                        ? "border-indigo-500 bg-indigo-600/20 text-indigo-200"
                        : "border-slate-600 bg-slate-800/40 text-slate-300 hover:border-slate-500"
                    }`}
                  >
                    <p className="text-sm font-medium">{t.label}</p>
                    <p className="text-[11px] text-slate-500">{t.hint}</p>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label
              htmlFor="meeting-title"
              className="text-xs font-semibold uppercase tracking-wider text-slate-400"
            >
              Title <span className="text-slate-600">(optional)</span>
            </label>
            <input
              id="meeting-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Acme onboarding call"
              className="mt-2 w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <label className="flex items-start gap-2 rounded-lg border border-slate-700/50 bg-slate-800/40 p-3 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-indigo-500"
            />
            <span>
              I&apos;ll let participants know the meeting is being transcribed. Felix
              transcribes live and <strong>discards the audio</strong> — only the
              transcript text is kept.
            </span>
          </label>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-300 transition-colors hover:border-slate-500"
          >
            Cancel
          </button>
          <button
            onClick={handleStart}
            disabled={!consent || starting}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
          >
            {starting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {starting ? "Starting…" : "Continue"}
          </button>
        </div>
      </div>
    </div>
  );
}
