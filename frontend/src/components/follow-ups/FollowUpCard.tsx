"use client";

import { useRef, useState } from "react";
import {
  Mail,
  X,
  Clock,
  CheckCircle,
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { FollowUp } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function daysFromNow(iso: string | null): number | null {
  if (!iso) return null;
  const due = new Date(iso);
  const now = new Date();
  return Math.round((due.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type ActivePanel = "idle" | "editing" | "confirm-close" | "snooze";
type Pending = "send" | "close" | "snooze" | null;

interface FollowUpCardProps {
  followUp: FollowUp;
  onUpdate: () => void;
}

export function FollowUpCard({ followUp, onUpdate }: FollowUpCardProps) {
  const [panel, setPanel] = useState<ActivePanel>("idle");
  const [pending, setPending] = useState<Pending>(null);
  const [editText, setEditText] = useState(followUp.auto_draft ?? "");
  const [snoozeDate, setSnoozeDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Track last attempted action for the retry button
  const lastAction = useRef<(() => Promise<void>) | null>(null);

  const days = daysFromNow(followUp.follow_up_by);
  const overdue = days !== null && days < 0;
  const isClosed = followUp.status === "closed";

  const draftPreview = followUp.auto_draft
    ? followUp.auto_draft.slice(0, 80) +
      (followUp.auto_draft.length > 80 ? "…" : "")
    : null;

  // ---- Actions ----

  async function doSend() {
    setPending("send");
    setError(null);
    try {
      await api.post(`/follow-ups/${followUp.id}/send`, {
        edited_text: editText,
      });
      setSuccessMsg("Sent");
      setPanel("idle");
      onUpdate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send");
    } finally {
      setPending(null);
    }
  }

  async function doClose() {
    setPending("close");
    setError(null);
    try {
      await api.post(`/follow-ups/${followUp.id}/close`);
      setSuccessMsg("Closed");
      setPanel("idle");
      onUpdate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to close");
    } finally {
      setPending(null);
    }
  }

  async function doSnooze() {
    if (!snoozeDate) return;
    setPending("snooze");
    setError(null);
    try {
      await api.patch(`/follow-ups/${followUp.id}`, {
        follow_up_by: snoozeDate,
      });
      setSuccessMsg("Snoozed");
      setPanel("idle");
      onUpdate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to snooze");
    } finally {
      setPending(null);
    }
  }

  function openPanel(next: ActivePanel, action?: () => Promise<void>) {
    setError(null);
    setSuccessMsg(null);
    setPanel(next);
    if (action) lastAction.current = action;
  }

  // ---- Render ----

  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4 transition-colors hover:bg-slate-800/60">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="truncate font-semibold text-slate-100">
              {followUp.to_email}
            </span>

            {days !== null && !isClosed && (
              <span
                className={`shrink-0 text-xs font-medium ${
                  overdue ? "text-red-400" : "text-slate-400"
                }`}
              >
                {overdue
                  ? `${Math.abs(days)}d overdue`
                  : days === 0
                    ? "Due today"
                    : `Due in ${days}d`}
              </span>
            )}
          </div>

          {followUp.subject && (
            <p className="mt-0.5 truncate text-sm text-slate-300">
              {followUp.subject}
            </p>
          )}
          {followUp.topic && (
            <p className="text-xs text-slate-500">{followUp.topic}</p>
          )}
        </div>

        {isClosed && (
          <span className="shrink-0 rounded-full bg-slate-700 px-2 py-0.5 text-xs text-slate-400">
            closed
          </span>
        )}
      </div>

      {/* Draft preview — only in idle state */}
      {draftPreview && panel === "idle" && (
        <p className="mt-2 border-l-2 border-slate-600 pl-2 text-xs italic text-slate-500">
          {draftPreview}
        </p>
      )}

      {/* ---- Edit & Send panel ---- */}
      {panel === "editing" && (
        <div className="mt-3 space-y-2">
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            rows={5}
            className="w-full resize-none rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none"
            placeholder="Edit your message…"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                lastAction.current = doSend;
                doSend();
              }}
              disabled={!!pending}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              {pending === "send" ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Mail className="h-3.5 w-3.5" />
              )}
              {pending === "send" ? "Sending…" : "Send"}
            </button>
            <button
              onClick={() => openPanel("idle")}
              disabled={!!pending}
              className="rounded-lg px-3 py-1.5 text-sm text-slate-400 transition-colors hover:text-slate-200 disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ---- Confirm close panel ---- */}
      {panel === "confirm-close" && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2">
          <span className="flex-1 text-sm text-amber-300">
            Close this follow-up?
          </span>
          <button
            onClick={() => {
              lastAction.current = doClose;
              doClose();
            }}
            disabled={!!pending}
            className="flex items-center gap-1 rounded-lg bg-amber-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-amber-500 disabled:opacity-50"
          >
            {pending === "close" ? (
              <RefreshCw className="h-3 w-3 animate-spin" />
            ) : (
              <CheckCircle className="h-3 w-3" />
            )}
            Confirm
          </button>
          <button
            onClick={() => openPanel("idle")}
            disabled={!!pending}
            className="rounded-lg px-2.5 py-1 text-xs text-slate-400 transition-colors hover:text-slate-200 disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      )}

      {/* ---- Snooze panel ---- */}
      {panel === "snooze" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={snoozeDate}
            onChange={(e) => setSnoozeDate(e.target.value)}
            min={new Date().toISOString().slice(0, 10)}
            className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none"
          />
          <button
            onClick={() => {
              lastAction.current = doSnooze;
              doSnooze();
            }}
            disabled={!snoozeDate || !!pending}
            className="flex items-center gap-1.5 rounded-lg bg-slate-700 px-3 py-1.5 text-sm text-slate-200 transition-colors hover:bg-slate-600 disabled:opacity-50"
          >
            {pending === "snooze" ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Clock className="h-3.5 w-3.5" />
            )}
            {pending === "snooze" ? "Saving…" : "Snooze"}
          </button>
          <button
            onClick={() => openPanel("idle")}
            disabled={!!pending}
            className="text-sm text-slate-400 transition-colors hover:text-slate-200 disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      )}

      {/* ---- Error ---- */}
      {error && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          {lastAction.current && (
            <button
              onClick={() => lastAction.current?.()}
              className="underline hover:no-underline"
            >
              Retry
            </button>
          )}
        </div>
      )}

      {/* ---- Success ---- */}
      {successMsg && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-emerald-400">
          <CheckCircle className="h-3.5 w-3.5" />
          {successMsg}
        </p>
      )}

      {/* ---- Action buttons (idle + not closed) ---- */}
      {panel === "idle" && !isClosed && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-700/50 pt-3">
          <button
            onClick={() => {
              setEditText(followUp.auto_draft ?? "");
              openPanel("editing", doSend);
            }}
            className="flex items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-700/50 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700"
          >
            <Mail className="h-3.5 w-3.5" />
            Edit &amp; Send
          </button>

          <button
            onClick={() => openPanel("confirm-close", doClose)}
            className="flex items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-700/50 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700"
          >
            <X className="h-3.5 w-3.5" />
            Close
          </button>

          <button
            onClick={() => {
              setSnoozeDate("");
              openPanel("snooze", doSnooze);
            }}
            className="flex items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-700/50 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700"
          >
            <Clock className="h-3.5 w-3.5" />
            Snooze
          </button>
        </div>
      )}
    </div>
  );
}
