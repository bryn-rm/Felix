"use client";

import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";

import type {
  MeetingTemplate,
  MeetingType,
  MeetingUserRole,
  StartMeetingInput,
} from "@/lib/types";
import { TEMPLATES } from "@/components/meetings/constants";

/** One selectable tile. The three pickers below differ only in their options. */
function OptionGrid<T extends string>({
  options,
  value,
  onChange,
  className = "grid-cols-2",
}: {
  options: readonly { value: T; label: string; hint: string }[];
  value: T | null;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div className={`mt-2 grid gap-2 ${className}`}>
      {options.map((option) => {
        const active = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`rounded-lg border p-2.5 text-left transition-colors ${
              active
                ? "border-indigo-500 bg-indigo-600/20 text-indigo-200"
                : "border-slate-600 bg-slate-800/40 text-slate-300 hover:border-slate-500"
            }`}
          >
            <p className="text-sm font-medium">{option.label}</p>
            <p className="text-[11px] text-slate-500">{option.hint}</p>
          </button>
        );
      })}
    </div>
  );
}

const MEETING_TYPES = [
  { value: "general", label: "General", hint: "Standard Live Assist" },
  { value: "interview", label: "Interview", hint: "Role-aware assistance" },
] as const satisfies readonly { value: MeetingType; label: string; hint: string }[];

const USER_ROLES = [
  { value: "candidate", label: "Candidate", hint: "You are being interviewed" },
  { value: "interviewer", label: "Interviewer", hint: "You are interviewing someone" },
] as const satisfies readonly { value: MeetingUserRole; label: string; hint: string }[];

// Interview is picked as a meeting type, not as a summary style.
const SUMMARY_STYLES = TEMPLATES.filter((option) => option.value !== "interview");

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
  onStart: (input: StartMeetingInput) => Promise<void>;
}) {
  const [template, setTemplate] = useState<MeetingTemplate>("general");
  const [meetingType, setMeetingType] = useState<MeetingType>("general");
  const [userRole, setUserRole] = useState<MeetingUserRole | null>(null);
  const [title, setTitle] = useState("");
  const [consent, setConsent] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The modal stays mounted while closed. Reset then so an Interview selection
  // from the previous meeting never becomes the next meeting's default.
  useEffect(() => {
    if (open) return;
    setTemplate("general");
    setMeetingType("general");
    setUserRole(null);
    setTitle("");
    setConsent(false);
    setError(null);
  }, [open]);

  if (!open) return null;

  async function handleStart() {
    if (
      !consent ||
      starting ||
      (meetingType === "interview" && userRole === null)
    ) return;
    setStarting(true);
    setError(null);
    try {
      await onStart({
        template: meetingType === "interview" ? "interview" : template,
        title: title.trim() || null,
        meeting_type: meetingType,
        user_role: meetingType === "interview" ? userRole : null,
      });
    } catch {
      // onClick discards the promise, so without this the modal just snaps back
      // to "Continue" on a failed /meetings/start (network drop, 429 budget,
      // 422 mode mismatch) and the user retries into the same wall.
      setError("Could not start the meeting. Please try again.");
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
            disabled={starting}
            aria-label="Close"
            className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200 disabled:pointer-events-none disabled:opacity-40"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-5 space-y-4">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Meeting type
            </label>
            <OptionGrid
              options={MEETING_TYPES}
              value={meetingType}
              onChange={(value) => {
                // Guarded: re-clicking the selected tile is a natural "confirm
                // my choice" gesture, and clearing the role there would
                // silently disable Continue with no explanation.
                if (value === meetingType) return;
                setMeetingType(value);
                setUserRole(null);
              }}
            />
          </div>

          {meetingType === "interview" && (
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Your role
              </label>
              <OptionGrid
                options={USER_ROLES}
                value={userRole}
                onChange={setUserRole}
              />
            </div>
          )}

          {meetingType === "general" && (
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Summary style
              </label>
              <OptionGrid
                options={SUMMARY_STYLES}
                value={template}
                onChange={setTemplate}
                className="grid-cols-2 sm:grid-cols-3"
              />
            </div>
          )}

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

        {error && (
          <p role="alert" className="mt-4 text-sm text-red-400">
            {error}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={starting}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-300 transition-colors hover:border-slate-500 disabled:pointer-events-none disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={handleStart}
            disabled={
              !consent ||
              starting ||
              (meetingType === "interview" && userRole === null)
            }
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
