import type { MeetingStatus, MeetingTemplate } from "@/lib/types";

export const TEMPLATES: {
  value: MeetingTemplate;
  label: string;
  hint: string;
}[] = [
  { value: "general", label: "General", hint: "Any meeting" },
  { value: "one_on_one", label: "1:1", hint: "Manager / report sync" },
  { value: "interview", label: "Interview", hint: "Hiring or job interview" },
  { value: "sales", label: "Sales", hint: "Prospect / customer call" },
  { value: "standup", label: "Standup", hint: "Team status update" },
  { value: "user_research", label: "User research", hint: "Discovery session" },
];

export function templateLabel(template: string | null | undefined): string {
  return TEMPLATES.find((t) => t.value === template)?.label ?? "General";
}

export const STATUS_META: Record<
  MeetingStatus,
  { label: string; className: string }
> = {
  idle: { label: "Idle", className: "bg-slate-700 text-slate-300" },
  recording: { label: "Recording", className: "bg-red-600/20 text-red-300" },
  processing: { label: "Processing", className: "bg-amber-600/20 text-amber-300" },
  done: { label: "Ready", className: "bg-emerald-600/20 text-emerald-300" },
  error: { label: "Error", className: "bg-red-600/20 text-red-300" },
};
