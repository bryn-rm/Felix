"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Archive, MailOpen } from "lucide-react";
import { api } from "@/lib/api";
import type { Email } from "@/lib/types";

interface EmailDetailProps {
  email: Email;
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Detect whether a string looks like HTML. */
function isHtml(body: string): boolean {
  return /<[a-z][\s\S]*>/i.test(body);
}

/** Inline component that sanitises and injects HTML using DOMPurify. */
function SafeHtmlBody({ html }: { html: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // DOMPurify is browser-only — import dynamically
    import("dompurify").then(({ default: DOMPurify }) => {
      const clean = DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        FORBID_ATTR: ["style"],
        FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form"],
      });
      if (ref.current) {
        ref.current.innerHTML = clean;
      }
    });
  }, [html]);

  return (
    <div
      ref={ref}
      className="prose prose-invert prose-sm max-w-none text-slate-300 [&_a]:text-indigo-400 [&_a:hover]:text-indigo-300"
    />
  );
}

export function EmailDetail({ email }: EmailDetailProps) {
  const router = useRouter();

  async function markRead() {
    try {
      await api.patch(`/emails/${email.id}`, { read: true });
    } catch {
      // best-effort
    }
  }

  async function archive() {
    try {
      await api.patch(`/emails/${email.id}`, { archived: true });
      router.push("/inbox");
    } catch {
      // best-effort
    }
  }

  const body = email.body ?? email.snippet ?? "";

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-700/50 bg-slate-800/40">
      {/* ── Top action bar ── */}
      <div className="flex shrink-0 items-center justify-between border-b border-slate-700/50 px-5 py-3">
        <button
          onClick={() => router.push("/inbox")}
          className="flex items-center gap-1.5 text-sm text-slate-400 transition-colors hover:text-slate-100"
        >
          <ArrowLeft className="h-4 w-4" />
          Back
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={markRead}
            className="flex items-center gap-1.5 rounded-md border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
          >
            <MailOpen className="h-3.5 w-3.5" />
            Mark read
          </button>
          <button
            onClick={archive}
            className="flex items-center gap-1.5 rounded-md border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
          >
            <Archive className="h-3.5 w-3.5" />
            Archive
          </button>
        </div>
      </div>

      {/* ── Scrollable email content ── */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {/* Subject */}
        <h1 className="mb-4 text-lg font-semibold leading-snug text-slate-100">
          {email.subject ?? "(no subject)"}
        </h1>

        {/* Header metadata */}
        <div className="mb-6 space-y-1 text-sm">
          <div className="flex items-baseline gap-2">
            <span className="w-8 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500">
              From
            </span>
            <span className="text-slate-200">
              {email.from_name ? (
                <>
                  <span className="font-medium">{email.from_name}</span>
                  <span className="ml-1.5 text-slate-400">
                    &lt;{email.from_email}&gt;
                  </span>
                </>
              ) : (
                <span className="font-medium">{email.from_email}</span>
              )}
            </span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="w-8 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500">
              Date
            </span>
            <span className="text-slate-400">{formatDateTime(email.received_at)}</span>
          </div>
          {email.category && (
            <div className="flex items-baseline gap-2">
              <span className="w-8 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500">
                Tag
              </span>
              <span className="rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-300">
                {email.category.replace(/_/g, " ")}
              </span>
            </div>
          )}
        </div>

        <hr className="mb-5 border-slate-700/50" />

        {/* Body */}
        {body ? (
          isHtml(body) ? (
            <SafeHtmlBody html={body} />
          ) : (
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-300">
              {body}
            </pre>
          )
        ) : (
          <p className="text-sm text-slate-500">(No message body)</p>
        )}
      </div>
    </div>
  );
}
