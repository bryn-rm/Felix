"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ThumbsDown } from "lucide-react";
import { api } from "@/lib/api";
import type { Email } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const minutes = Math.floor(diffMs / 60_000);
  const hours = Math.floor(diffMs / 3_600_000);
  const days = Math.floor(diffMs / 86_400_000);
  if (minutes < 60) return `${minutes}m`;
  if (hours < 24) return `${hours}h`;
  if (days < 7) return date.toLocaleDateString("en", { weekday: "short" });
  return date.toLocaleDateString("en", { month: "short", day: "numeric" });
}

function avatarColor(email: string): string {
  const palette = [
    "bg-rose-600",
    "bg-orange-600",
    "bg-amber-600",
    "bg-emerald-600",
    "bg-teal-600",
    "bg-cyan-600",
    "bg-blue-600",
    "bg-violet-600",
    "bg-fuchsia-600",
  ];
  let hash = 0;
  for (const ch of email) hash = ((hash * 31) + ch.charCodeAt(0)) & 0xffff;
  return palette[hash % palette.length];
}

function senderInitials(name: string | null, email: string): string {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2)
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

// ---------------------------------------------------------------------------
// Badge + dot configs
// ---------------------------------------------------------------------------

const URGENCY_CLASSES: Record<string, string> = {
  critical: "bg-red-500/20 text-red-400 ring-red-500/30",
  high: "bg-orange-500/20 text-orange-400 ring-orange-500/30",
  medium: "bg-yellow-500/20 text-yellow-400 ring-yellow-500/30",
  low: "bg-slate-500/20 text-slate-400 ring-slate-500/30",
};

const CATEGORY_DOT: Record<string, string> = {
  action_required: "bg-red-500",
  fyi: "bg-slate-400",
  waiting_on: "bg-blue-500",
  newsletter: "bg-purple-500",
  automated: "bg-slate-600",
  vip: "bg-indigo-500",
};

// ---------------------------------------------------------------------------
// Category correction popover
// ---------------------------------------------------------------------------

const CATEGORIES = [
  "action_required",
  "fyi",
  "waiting_on",
  "newsletter",
  "automated",
] as const;

type Category = (typeof CATEGORIES)[number];

function CorrectionPopover({
  emailId,
  onClose,
}: {
  emailId: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [submitting, setSubmitting] = useState<string | null>(null);

  // Close on outside click
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [onClose]);

  async function handleSelect(correction: Category) {
    setSubmitting(correction);
    try {
      await api.post("/eval/feedback", {
        ai_call_id: emailId,
        feature: "triage",
        rating: 1,
        correction,
        notes: null,
      });
    } catch {
      // Best-effort — don't surface errors for feedback
    } finally {
      onClose();
    }
  }

  return (
    <div
      ref={ref}
      className="absolute right-0 top-6 z-50 w-44 rounded-lg border border-slate-600 bg-slate-800 py-1 shadow-xl"
    >
      <p className="px-3 py-1.5 text-xs font-medium text-slate-400">
        Correct category
      </p>
      {CATEGORIES.map((cat) => (
        <button
          key={cat}
          disabled={submitting === cat}
          onClick={() => handleSelect(cat)}
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-slate-200 transition-colors hover:bg-slate-700 disabled:opacity-50"
        >
          <span
            className={`h-2 w-2 rounded-full ${CATEGORY_DOT[cat] ?? "bg-slate-400"}`}
          />
          {cat.replace(/_/g, " ")}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EmailCard
// ---------------------------------------------------------------------------

interface EmailCardProps {
  email: Email;
}

export function EmailCard({ email }: EmailCardProps) {
  const router = useRouter();
  const [showCorrection, setShowCorrection] = useState(false);
  const [hovered, setHovered] = useState(false);

  const bgColor = avatarColor(email.from_email);
  const initials = senderInitials(email.from_name, email.from_email);
  const urgencyClass =
    email.urgency ? (URGENCY_CLASSES[email.urgency] ?? URGENCY_CLASSES.low) : null;
  const dotClass =
    email.category ? (CATEGORY_DOT[email.category] ?? "bg-slate-400") : "bg-slate-400";

  return (
    <div
      className="group relative flex cursor-pointer items-start gap-3 rounded-lg border border-slate-700/50 bg-slate-800/40 px-4 py-3 transition-colors hover:bg-slate-800/80"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => { setHovered(false); }}
      onClick={() => router.push(`/inbox/${email.id}`)}
    >
      {/* Sender avatar */}
      <div
        className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${bgColor}`}
      >
        {initials}
      </div>

      {/* Main content */}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-semibold text-slate-100">
            {email.from_name ?? email.from_email}
          </span>
          {email.from_name && (
            <span className="hidden truncate text-xs text-slate-500 sm:block">
              {email.from_email}
            </span>
          )}
        </div>
        <p className="truncate text-sm text-slate-300">
          {email.subject ?? "(no subject)"}
        </p>
        {email.snippet && (
          <p className="mt-0.5 truncate text-xs text-slate-500">{email.snippet}</p>
        )}
      </div>

      {/* Right metadata */}
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <div className="flex items-center gap-1.5">
          {urgencyClass && (
            <span
              className={`rounded px-1.5 py-0.5 text-xs font-medium ring-1 ${urgencyClass}`}
            >
              {email.urgency}
            </span>
          )}
          <span className="text-xs text-slate-500">
            {formatTime(email.received_at)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${dotClass}`}
            title={email.category ?? undefined}
          />
          {/* Thumbs-down feedback trigger — visible on hover */}
          <div className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowCorrection((v) => !v);
              }}
              aria-label="Correct category"
              className={`rounded p-0.5 text-slate-500 transition-all hover:text-slate-300 ${
                hovered || showCorrection ? "opacity-100" : "opacity-0"
              }`}
            >
              <ThumbsDown className="h-3.5 w-3.5" />
            </button>
            {showCorrection && (
              <CorrectionPopover
                emailId={email.id}
                onClose={() => setShowCorrection(false)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
