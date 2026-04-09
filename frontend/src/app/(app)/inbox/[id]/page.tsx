"use client";

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

export default function EmailDetailPage({ params }: PageProps) {
  const { id } = params;

  const {
    data: email,
    isLoading,
    error,
  } = useSWR<Email>(`email-${id}`, () => api.get<Email>(`/emails/${id}`));

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

  return (
    <div className="flex h-full gap-5 overflow-hidden">
      {/* ── Left column: 60% ── */}
      <div className="min-w-0 flex-[3] overflow-hidden">
        <EmailDetail email={email} />
      </div>

      {/* ── Right column: 40% ── */}
      <div className="flex w-[38%] shrink-0 flex-col gap-4 overflow-y-auto">
        <DraftPanel emailId={id} />
        <ContactSidebar senderEmail={email.from_email} />
      </div>
    </div>
  );
}
