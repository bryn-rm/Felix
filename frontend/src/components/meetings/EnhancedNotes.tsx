"use client";

import type { EnhancedNote } from "@/lib/types";

/**
 * Enhanced notes: the user's own notes verbatim (origin:'user', rendered bright)
 * interleaved with AI-added context (origin:'ai', rendered grey). The contrast
 * makes it obvious at a glance what the user wrote vs. what Felix added.
 */
export function EnhancedNotes({ notes }: { notes: EnhancedNote[] }) {
  if (!notes || notes.length === 0) {
    return (
      <p className="text-sm text-slate-500">No enhanced notes for this meeting.</p>
    );
  }

  return (
    <div className="space-y-2">
      {notes.map((note, i) => (
        <p
          key={i}
          className={
            note.origin === "user"
              ? "whitespace-pre-wrap text-sm font-medium text-slate-100"
              : "whitespace-pre-wrap text-sm text-slate-400"
          }
        >
          {note.text}
        </p>
      ))}
      <p className="pt-2 text-[11px] text-slate-600">
        Bright lines are your notes, verbatim. Grey lines are context Felix added.
      </p>
    </div>
  );
}
