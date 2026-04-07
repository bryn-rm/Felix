"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Send, Trash2, RefreshCw, Loader2, Sparkles } from "lucide-react";
import { useDraft } from "@/hooks/useDraft";
import { api } from "@/lib/api";

interface DraftPanelProps {
  emailId: string;
}

export function DraftPanel({ emailId }: DraftPanelProps) {
  const router = useRouter();
  const { draft, draftText, state, error, send, discard } = useDraft(emailId);
  const [editedText, setEditedText] = useState("");
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const [polishing, setPolishing] = useState(false);
  const [polishError, setPolishError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  async function handlePolish() {
    if (polishing || !editedText.trim()) return;
    setPolishing(true);
    setPolishError(null);
    try {
      const res = await api.post<{ polished: string }>("/polish/draft", {
        text: editedText,
      });
      if (res.polished) setEditedText(res.polished);
    } catch (err) {
      setPolishError(
        err instanceof Error ? err.message : "Couldn't polish draft.",
      );
    } finally {
      setPolishing(false);
    }
  }

  // Keep textarea in sync with streaming / initial text
  useEffect(() => {
    if (state === "generating" || state === "loading") {
      setEditedText(draftText);
    } else if (state === "ready" && editedText === "") {
      // Only seed once when transitioning from loading/generating → ready
      setEditedText(draftText);
    }
  }, [draftText, state]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll textarea to bottom while streaming
  useEffect(() => {
    if (state === "generating" && textareaRef.current) {
      const el = textareaRef.current;
      el.scrollTop = el.scrollHeight;
    }
  }, [draftText, state]);

  // Navigate back after successful send
  useEffect(() => {
    if (state === "sent") {
      const t = setTimeout(() => router.push("/inbox"), 1200);
      return () => clearTimeout(t);
    }
  }, [state, router]);

  async function handleSend() {
    await send(editedText);
  }

  async function handleDiscard() {
    setShowDiscardConfirm(false);
    await discard();
    router.push("/inbox");
  }

  // ── Sent confirmation ──────────────────────────────────────────────────────
  if (state === "sent") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-600/10 p-6 text-center">
        <Send className="h-8 w-8 text-emerald-400" />
        <p className="text-sm font-medium text-emerald-300">Email sent!</p>
        <p className="text-xs text-slate-500">Returning to inbox…</p>
      </div>
    );
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (state === "error") {
    return (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-slate-700/50 bg-slate-800/40 p-6 text-center">
        <p className="text-sm text-red-400">
          {error ?? "Something went wrong."}
        </p>
        <button
          onClick={() => window.location.reload()}
          className="flex items-center gap-1.5 rounded-md bg-slate-700 px-4 py-2 text-sm text-slate-200 transition-colors hover:bg-slate-600"
        >
          <RefreshCw className="h-4 w-4" />
          Retry
        </button>
      </div>
    );
  }

  // ── Loading (initial fetch) ────────────────────────────────────────────────
  if (state === "loading") {
    return (
      <div className="flex h-full items-center justify-center rounded-xl border border-slate-700/50 bg-slate-800/40 p-6">
        <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
      </div>
    );
  }

  const isGenerating = state === "generating";
  const isSending = state === "sending";
  const isInteractive = state === "ready" && !isSending;

  return (
    <>
      <div className="flex flex-col gap-3 rounded-xl border border-slate-700/50 bg-slate-800/40 p-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-200">
            {isGenerating ? "Generating draft…" : "Draft reply"}
          </h3>
          {isGenerating && (
            <span className="flex items-center gap-1.5 text-xs text-indigo-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Writing…
            </span>
          )}
        </div>

        {/* Textarea */}
        <div className="relative">
          <textarea
            ref={textareaRef}
            value={isGenerating ? draftText : editedText}
            onChange={(e) => {
              if (isInteractive) setEditedText(e.target.value);
            }}
            readOnly={!isInteractive}
            rows={12}
            className={[
              "w-full resize-none rounded-lg border bg-slate-900 px-4 py-3 text-sm leading-relaxed text-slate-100",
              "placeholder:text-slate-600 focus:outline-none focus:ring-1",
              isInteractive
                ? "border-slate-600 focus:border-indigo-500 focus:ring-indigo-500"
                : "cursor-default border-slate-700 text-slate-300",
            ].join(" ")}
            placeholder={isGenerating ? "" : "Edit your reply…"}
          />
          {/* Blinking cursor indicator while streaming */}
          {isGenerating && (
            <span
              className="pointer-events-none absolute bottom-3 right-3 inline-block h-4 w-0.5 animate-pulse bg-indigo-400"
              aria-hidden
            />
          )}
        </div>

        {/* Footer: char count + actions */}
        <div className="flex items-center justify-between">
          {isInteractive ? (
            <span className="text-xs text-slate-500">
              {polishError ? (
                <span className="text-red-400">{polishError}</span>
              ) : (
                <>{editedText.length.toLocaleString()} chars</>
              )}
            </span>
          ) : (
            <span />
          )}

          <div className="flex items-center gap-2">
            {/* Polish */}
            {isInteractive && (
              <button
                onClick={handlePolish}
                disabled={polishing || editedText.trim().length === 0}
                title="Polish — improve tone, grammar and clarity"
                className="flex items-center gap-1.5 rounded-md border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-indigo-500/50 hover:text-indigo-300 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {polishing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                {polishing ? "Polishing…" : "Polish"}
              </button>
            )}

            {/* Discard */}
            {draft && isInteractive && (
              <button
                onClick={() => setShowDiscardConfirm(true)}
                className="flex items-center gap-1.5 rounded-md border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-red-500/50 hover:text-red-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Discard
              </button>
            )}

            {/* Send */}
            <button
              onClick={handleSend}
              disabled={!isInteractive || editedText.trim().length === 0}
              className="flex items-center gap-1.5 rounded-md bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {isSending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              {isSending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      </div>

      {/* ── Discard confirmation dialog ─────────────────────────────────────── */}
      {showDiscardConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
            <h4 className="mb-2 text-sm font-semibold text-slate-100">
              Discard this draft?
            </h4>
            <p className="mb-5 text-sm text-slate-400">
              This will permanently delete the generated draft. You can
              regenerate it any time.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDiscardConfirm(false)}
                className="rounded-md border border-slate-600 px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:text-slate-100"
              >
                Cancel
              </button>
              <button
                onClick={handleDiscard}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500"
              >
                Discard
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
