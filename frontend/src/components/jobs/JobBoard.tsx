"use client";

import { useState } from "react";
import type { JobBoard as JobBoardData, JobStatus } from "@/lib/types";
import { JobCard } from "./JobCard";
import { BOARD_COLUMNS, DROPPABLE_STATUSES } from "./constants";

export function JobBoard({
  board,
  onMove,
}: {
  board: JobBoardData;
  onMove: (id: string, status: JobStatus) => Promise<void>;
}) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);

  async function handleDrop(columnKey: string) {
    const id = dragId;
    setDragId(null);
    setOverKey(null);
    if (!id) return;
    if (!DROPPABLE_STATUSES.includes(columnKey as JobStatus)) return; // "closed" is display-only
    await onMove(id, columnKey as JobStatus);
  }

  return (
    <div className="flex h-full gap-3 overflow-x-auto pb-4">
      {BOARD_COLUMNS.map(({ key, label }) => {
        const cards = board.columns[key] ?? [];
        const droppable = DROPPABLE_STATUSES.includes(key as JobStatus);
        const isOver = overKey === key && droppable;
        return (
          <div
            key={key}
            onDragOver={(e) => {
              if (!droppable) return;
              e.preventDefault();
              setOverKey(key);
            }}
            onDragLeave={() => setOverKey((k) => (k === key ? null : k))}
            onDrop={(e) => {
              e.preventDefault();
              void handleDrop(key);
            }}
            className={[
              "flex w-64 shrink-0 flex-col rounded-lg border bg-slate-900/40",
              isOver ? "border-indigo-500 bg-indigo-600/5" : "border-slate-700/50",
            ].join(" ")}
          >
            <div className="flex items-center justify-between border-b border-slate-700/50 px-3 py-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                {label}
              </span>
              <span className="rounded-full bg-slate-700/60 px-1.5 text-[10px] font-semibold text-slate-300">
                {cards.length}
              </span>
            </div>
            <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
              {cards.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  dragging={dragId === job.id}
                  onDragStart={setDragId}
                  onDragEnd={() => {
                    setDragId(null);
                    setOverKey(null);
                  }}
                />
              ))}
              {cards.length === 0 && (
                <p className="px-1 py-4 text-center text-xs text-slate-600">
                  {droppable ? "Drop here" : "—"}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
