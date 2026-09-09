import type { ProjectActivity as Activity } from "@/hooks/useProjects";
import { sourceLabels } from "./SourcePicker";

const labels: Record<string, string> = {
  created: "Project created", renamed: "Project renamed", description_changed: "Description edited",
  target_date_changed: "Target date changed", archived: "Project archived", reopened: "Project reopened",
  email_received: "Email received", email_sent: "Email sent", meeting_occurred: "Meeting occurred",
  commitment_captured: "Commitment captured", commitment_resolved: "Commitment resolved",
  scope_changed: "Confirmed scope revised", record_created: "Project record added",
  record_changed: "Project record changed", commitment_changed: "Commitment changed",
};

export function ProjectActivityList({ activity }: { activity: Activity[] }) {
  if (!activity.length) return <p className="text-sm text-slate-400">No activity yet.</p>;
  return <ol className="space-y-3">{activity.map((item) => {
    const label = item.action === "linked" || item.action === "unlinked"
      ? `${item.source_kind ? sourceLabels[item.source_kind] : "Source"} ${item.action}`
      : labels[item.action] ?? item.action;
    return <li key={item.id} className="border-l-2 border-slate-700 pl-3">
      <p className="text-sm text-slate-200">{label}{item.details.title && <>: {item.details.title}</>}</p>
      {(item.action === "renamed" || item.action === "target_date_changed") && <p className="text-xs text-slate-400">{item.details.before || "Not set"} → {item.details.after || "Not set"}</p>}
      {item.details.status && <p className="text-xs text-slate-400">{item.details.status}</p>}
      <p className="text-xs text-slate-400">{item.event_type === "source" ? "Original source event" : "Project action"} · <time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString()}</time></p>
    </li>;
  })}</ol>;
}
