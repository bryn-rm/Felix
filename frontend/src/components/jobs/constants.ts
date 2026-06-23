import type { JobBoardColumnKey, JobStatus } from "@/lib/types";

/** Ordered board columns. Positive ladder + a collapsed "closed" sink. */
export const BOARD_COLUMNS: { key: JobBoardColumnKey; label: string }[] = [
  { key: "saved", label: "Saved" },
  { key: "applied", label: "Applied" },
  { key: "phone_screen", label: "Phone Screen" },
  { key: "interview", label: "Interview" },
  { key: "offer", label: "Offer" },
  { key: "closed", label: "Closed" },
];

/** Columns that accept drag-drop (each maps to a single status). The "closed"
 * column is display-only — terminal statuses (rejected/withdrawn/accepted) are
 * set from the detail page since the target is ambiguous. */
export const DROPPABLE_STATUSES: JobStatus[] = [
  "saved",
  "applied",
  "phone_screen",
  "interview",
  "offer",
];

export const ALL_STATUSES: { value: JobStatus; label: string }[] = [
  { value: "saved", label: "Saved" },
  { value: "applied", label: "Applied" },
  { value: "phone_screen", label: "Phone Screen" },
  { value: "interview", label: "Interview" },
  { value: "offer", label: "Offer" },
  { value: "rejected", label: "Rejected" },
  { value: "accepted", label: "Accepted" },
  { value: "withdrawn", label: "Withdrawn" },
];

export function statusLabel(status: JobStatus): string {
  return ALL_STATUSES.find((s) => s.value === status)?.label ?? status;
}
