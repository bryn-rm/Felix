"use client";

import { useState } from "react";
import { useFollowUps, type FollowUpFilter } from "@/hooks/useFollowUps";
import { FollowUpCard } from "@/components/follow-ups/FollowUpCard";

const TABS: { label: string; value: FollowUpFilter }[] = [
  { label: "All", value: "all" },
  { label: "Overdue", value: "overdue" },
  { label: "Waiting", value: "waiting" },
  { label: "Closed", value: "closed" },
];

export default function FollowUpsPage() {
  const [filter, setFilter] = useState<FollowUpFilter>("all");
  const { followUps, counts, isLoading, error, mutate } = useFollowUps(filter);

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <h1 className="text-xl font-semibold text-slate-100">Follow-ups</h1>

      {/* Filter tabs */}
      <div className="flex w-fit gap-1 rounded-lg border border-slate-700 bg-slate-800/40 p-1">
        {TABS.map(({ label, value }) => {
          const count = counts[value];
          const active = filter === value;
          return (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={[
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                active
                  ? "bg-slate-700 text-slate-100"
                  : "text-slate-400 hover:text-slate-200",
              ].join(" ")}
            >
              {label}
              {count > 0 && (
                <span
                  className={[
                    "rounded-full px-1.5 py-0.5 text-xs font-semibold leading-none",
                    value === "overdue"
                      ? "bg-red-500 text-white"
                      : "bg-slate-600 text-slate-300",
                  ].join(" ")}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Loading skeletons */}
      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-28 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
              style={{ animationDelay: `${i * 80}ms` }}
            />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <p className="text-sm text-red-400">
          Failed to load follow-ups: {error.message}
        </p>
      )}

      {/* Empty state */}
      {!isLoading && !error && followUps.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <p className="text-base font-medium text-slate-300">
            No follow-ups — you&apos;re on top of it
          </p>
          <p className="text-sm text-slate-500">
            {filter !== "all"
              ? `No ${filter} follow-ups right now.`
              : "Nothing pending."}
          </p>
        </div>
      )}

      {/* List */}
      {!isLoading && !error && followUps.length > 0 && (
        <div className="space-y-3 pb-6">
          {followUps.map((fu) => (
            <FollowUpCard key={fu.id} followUp={fu} onUpdate={mutate} />
          ))}
        </div>
      )}
    </div>
  );
}
