"use client";

import { useState } from "react";
import useSWR from "swr";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Email } from "@/lib/types";
import { EmailDetail } from "@/components/email/EmailDetail";
import { DraftPanel } from "@/components/email/DraftPanel";
import { ContactSidebar } from "@/components/email/ContactSidebar";

interface PageProps {
  params: { id: string };
}

type MobileTab = "email" | "draft" | "context";

export default function EmailDetailPage({ params }: PageProps) {
  const { id } = params;

  const {
    data: email,
    isLoading,
    error,
  } = useSWR<Email>(`email-${id}`, () => api.get<Email>(`/emails/${id}`));

  const [mobileTab, setMobileTab] = useState<MobileTab>("email");
  const [draftVisited, setDraftVisited] = useState(false);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
      </div>
    );
  }

  if (error || !email) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-500">
        <p>Could not load this email.</p>
        <button
          onClick={() => window.location.reload()}
          className="text-indigo-400 hover:text-indigo-300 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  function selectTab(next: MobileTab) {
    setMobileTab(next);
    if (next === "draft") setDraftVisited(true);
  }

  const tabs: { id: MobileTab; label: string; showDot: boolean }[] = [
    { id: "email", label: "Email", showDot: false },
    { id: "draft", label: "Draft", showDot: !draftVisited },
    { id: "context", label: "Context", showDot: false },
  ];

  // Mobile uses tabbed single-column; desktop (md+) uses two-column row.
  // Each child component is rendered exactly once — the desktop right-column
  // wrapper uses `display: contents` on mobile so DraftPanel and ContactSidebar
  // flow as direct flex items of the outer container; on md+ it becomes a real
  // 38%-width column that stacks them.
  return (
    <div className="flex h-full flex-col overflow-hidden md:flex-row md:gap-5">
      {/* Mobile tab strip — hidden on desktop */}
      <div
        role="tablist"
        aria-label="Email view"
        className="flex shrink-0 border-b border-white/[0.04] bg-[#080f1e] md:hidden"
      >
        {tabs.map((t) => {
          const active = mobileTab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={active}
              onClick={() => selectTab(t.id)}
              className={[
                "relative flex flex-1 items-center justify-center gap-1.5 py-3 text-sm font-medium transition-colors min-h-[44px]",
                active
                  ? "text-indigo-400"
                  : "text-slate-500 hover:text-slate-300",
              ].join(" ")}
            >
              {t.label}
              {t.showDot && (
                <span
                  aria-hidden
                  className="inline-block h-1.5 w-1.5 rounded-full bg-indigo-400"
                />
              )}
              {active && (
                <span
                  aria-hidden
                  className="absolute inset-x-0 bottom-0 h-0.5 bg-indigo-400"
                />
              )}
            </button>
          );
        })}
      </div>

      {/* EmailDetail — left column on desktop, "Email" tab on mobile */}
      <div
        className={[
          "min-w-0 flex-1 overflow-hidden md:flex-[3]",
          mobileTab === "email" ? "" : "hidden md:block",
        ].join(" ")}
      >
        <EmailDetail email={email} />
      </div>

      {/* Right-column wrapper. On mobile it's `display: contents` so its two
          children flow as siblings of EmailDetail in the outer flex; on md+
          it becomes a real 38%-wide flex column that stacks them. */}
      <div className="contents md:flex md:w-[38%] md:shrink-0 md:flex-col md:gap-4 md:overflow-y-auto">
        <div
          className={[
            "min-w-0 flex-1 overflow-y-auto md:flex-none md:overflow-visible",
            mobileTab === "draft" ? "" : "hidden md:block",
          ].join(" ")}
        >
          <DraftPanel emailId={id} />
        </div>
        <div
          className={[
            "min-w-0 flex-1 overflow-y-auto md:flex-none md:overflow-visible",
            mobileTab === "context" ? "" : "hidden md:block",
          ].join(" ")}
        >
          <ContactSidebar senderEmail={email.from_email} />
        </div>
      </div>
    </div>
  );
}
