"use client";

import { Inbox } from "lucide-react";
import { EmailCard } from "@/components/inbox/EmailCard";
import { useEmails } from "@/hooks/useEmails";

interface EmailListProps {
  category?: string;
  search?: string;
}

function SkeletonCard() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-slate-700/50 bg-slate-800/40 px-4 py-3">
      <div className="h-9 w-9 shrink-0 animate-pulse rounded-full bg-slate-700" />
      <div className="flex-1 space-y-2 py-0.5">
        <div className="h-3 w-1/3 animate-pulse rounded bg-slate-700" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-slate-700" />
        <div className="h-2.5 w-1/2 animate-pulse rounded bg-slate-700/60" />
      </div>
      <div className="flex flex-col items-end gap-2">
        <div className="h-4 w-12 animate-pulse rounded bg-slate-700" />
        <div className="h-2 w-2 animate-pulse rounded-full bg-slate-700" />
      </div>
    </div>
  );
}

export function EmailList({ category, search = "" }: EmailListProps) {
  const { emails, isLoading, error, hasMore, loadMore } = useEmails({
    category,
    limit: 25,
  });

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <p className="text-sm text-slate-400">Could not load emails.</p>
        <button
          onClick={() => window.location.reload()}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          Retry
        </button>
      </div>
    );
  }

  // Client-side search filter
  const needle = search.trim().toLowerCase();
  const filtered = needle
    ? emails.filter(
        (e) =>
          e.subject?.toLowerCase().includes(needle) ||
          e.from_name?.toLowerCase().includes(needle) ||
          e.from_email.toLowerCase().includes(needle),
      )
    : emails;

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Inbox className="h-10 w-10 text-slate-600" />
        <p className="text-sm text-slate-400">
          {needle
            ? "No emails match your search."
            : "No emails in this category."}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {filtered.map((email) => (
        <EmailCard key={email.id} email={email} />
      ))}

      {hasMore && !needle && (
        <div className="pt-3 text-center">
          <button
            onClick={loadMore}
            className="rounded-md border border-slate-600 px-5 py-2 text-sm font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
          >
            Load more
          </button>
        </div>
      )}
    </div>
  );
}
