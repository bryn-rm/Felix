import type {
  Meeting,
  MeetingStatus,
  MeetingTemplate,
} from "@/lib/types";

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

/**
 * The Live Assist viewer route for a meeting — an ordinary authenticated app
 * path, and deliberately nothing more. It is what the phone handoff QR encodes,
 * so it carries no token and confers no access: whoever opens it signs in as
 * themselves and the server's ownership checks decide the rest.
 *
 * One definition so the link the laptop shows and the link it encodes cannot
 * drift apart.
 */
export function liveAssistViewerPath(meetingId: string): string {
  return `/meetings/live/${meetingId}/viewer`;
}

export type AssistMeetingMode =
  | "general"
  | "interview_candidate"
  | "interview_interviewer"
  | "legacy_interview";

type MeetingMode = Pick<Meeting, "meeting_type" | "user_role" | "template">;

/**
 * Mirror of `resolve_assist_meeting_mode` in backend/app/models/meeting.py —
 * change both together. Everything role-dependent in the UI derives from this
 * one function so the badge can never disagree with the behaviour it labels.
 *
 * Explicit configuration wins; `template === "interview"` is only the fallback
 * for legacy rows written before migration 020, which is what `meeting_type ===
 * null` marks. Any other explicit value fails closed to "general" (the backend
 * does the same) rather than inheriting the template.
 */
export function resolveAssistMeetingMode(
  meeting: MeetingMode | null | undefined,
): AssistMeetingMode {
  if (!meeting) return "general";
  if (meeting.meeting_type === "interview") {
    if (meeting.user_role === "candidate") return "interview_candidate";
    if (meeting.user_role === "interviewer") return "interview_interviewer";
    return "general";
  }
  if (meeting.meeting_type == null && meeting.template === "interview") {
    return "legacy_interview";
  }
  return "general";
}

/** Is the user the one being interviewed? Gates the whole candidate solve UI. */
export function usesCandidateInterviewAssist(
  meeting: MeetingMode | null | undefined,
): boolean {
  const mode = resolveAssistMeetingMode(meeting);
  return mode === "interview_candidate" || mode === "legacy_interview";
}

/**
 * Header badge text, or null when there is nothing worth announcing. Derived
 * from the same mode as the behaviour: a legacy interview really does run
 * candidate assist, so it must not sit there unlabelled just because no
 * explicit role was ever stored for it.
 */
export function assistModeLabel(
  meeting: MeetingMode | null | undefined,
): string | null {
  switch (resolveAssistMeetingMode(meeting)) {
    case "interview_candidate":
      return "Interview · Candidate";
    case "interview_interviewer":
      return "Interview · Interviewer";
    case "legacy_interview":
      return "Interview";
    default:
      return null;
  }
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
