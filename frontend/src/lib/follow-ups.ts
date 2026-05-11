import type { FollowUp } from "@/lib/types";

export function isOverdue(fu: FollowUp, now: Date = new Date()): boolean {
  if (fu.status === "closed") return false;
  if (!fu.follow_up_by) return false;
  return new Date(fu.follow_up_by) < now;
}
