export interface Email {
  id: string;
  thread_id: string;
  from_email: string;
  from_name: string | null;
  subject: string | null;
  body: string | null;
  snippet: string | null;
  received_at: string;
  category: string | null;
  urgency: string | null;
  sentiment: string | null;
  topic: string | null;
  triage_json: Record<string, unknown> | null;
  read: boolean;
  archived: boolean;
}

export interface Draft {
  id: string;
  email_id: string;
  draft_text: string;
  status: string;
  edited_text: string | null;
  generated_at: string;
  sent_at: string | null;
}

export type SentimentTrend = "improving" | "stable" | "deteriorating";

export interface Contact {
  email: string;
  name: string | null;
  company: string | null;
  role: string | null;
  vip: boolean;
  relationship_strength: number;
  total_emails: number;
  last_contacted: string | null;
  sentiment_trend: SentimentTrend | null;
  topics_discussed: string[];
  open_commitments: string[];
  their_open_commitments: string[];
}

export interface FollowUp {
  id: string;
  email_id: string | null;
  to_email: string;
  subject: string | null;
  topic: string | null;
  sent_at: string | null;
  follow_up_by: string | null;
  status: string;
  urgency: string | null;
  auto_draft: string | null;
}

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  attendees: string[];
  location: string | null;
  description: string | null;
  is_focus_block: boolean;
  hangout_link?: string | null;
  html_link?: string | null;
  organizer?: string | null;
  status?: string | null;
}

export interface Briefing {
  id: string;
  date: string;
  text: string;
  audio_url: string | null;
  generated_at: string;
  listened_at: string | null;
}

export type MeetingPrepMode = "off" | "email_only" | "in_app_only" | "both";

export interface EnergyProfile {
  deep_work?: string[]; // e.g. ["09:00-12:00"]
  meetings?: string[];  // e.g. ["14:00-17:00"]
}

export interface StyleProfile {
  last_analyzed?: string;
  formality_score?: number;
  avg_word_count?: number;
  common_greetings?: string[];
  common_sign_offs?: string[];
  [key: string]: unknown;
}

export interface Settings {
  display_name: string | null;
  timezone: string;
  briefing_time: string;
  digest_mode: boolean;
  digest_times: string[];
  vip_contacts: string[];
  style_profile: StyleProfile | null;
  meeting_prep_mode: MeetingPrepMode;
  job_search_mode: boolean;
  energy_profile: EnergyProfile | null;
  felix_voice_id: string | null;
}

export type TemplateCategory = "reply" | "outreach" | "follow_up" | "other";

export interface Template {
  id: string;
  name: string;
  subject_template: string;
  body_template: string;
  category: TemplateCategory | null;
}

export interface AiFeedback {
  ai_call_id: string;
  feature: string;
  rating: number;
  correction: string | null;
  notes: string | null;
}

export interface Commitment {
  id: string;
  source_email_id: string | null;
  source_kind: "inbound" | "sent";
  direction: "owed_by_user" | "owed_to_user";
  counterparty_email: string | null;
  counterparty_name: string | null;
  text: string;
  source_quote: string | null;
  deadline: string | null;
  confidence: number;
  status: "open" | "done" | "dropped" | "rescued";
  created_at: string;
  resolved_at: string | null;
}

// ---------------------------------------------------------------------------
// Job Search Mode
// ---------------------------------------------------------------------------

export type JobStatus =
  | "saved"
  | "applied"
  | "phone_screen"
  | "interview"
  | "offer"
  | "rejected"
  | "accepted"
  | "withdrawn";

export interface JobApplication {
  id: string;
  thread_id: string | null;
  company: string;
  role_title: string;
  location: string | null;
  job_url: string | null;
  status: JobStatus;
  source: "manual" | "email" | "calendar";
  contact_name: string | null;
  contact_email: string | null;
  compensation: string | null;
  notes: string | null;
  applied_at: string | null;
  last_activity_at: string | null;
  next_action: string | null;
  next_action_at: string | null;
  confidence: number;
  created_at: string;
  updated_at: string;
  /** Computed server-side: next_action_at is in the past. */
  is_due?: boolean;
}

export type JobEventType =
  | "applied"
  | "email_in"
  | "email_out"
  | "interview_scheduled"
  | "status_change"
  | "note"
  | "follow_up_sent";

export interface JobEvent {
  id: string;
  job_id: string;
  event_type: JobEventType;
  title: string | null;
  detail: string | null;
  source_kind: "email" | "calendar" | "manual" | null;
  source_id: string | null;
  occurred_at: string;
  created_at: string;
}

export interface JobSuggestion {
  id: string;
  source_kind: "email" | "calendar" | null;
  source_id: string | null;
  thread_id: string | null;
  company: string | null;
  role_title: string | null;
  contact_name: string | null;
  contact_email: string | null;
  proposed_status: JobStatus | null;
  proposed_job_id: string | null;
  summary: string | null;
  confidence: number | null;
  status: "pending" | "accepted" | "dismissed" | "auto_dismissed";
  resolved_at: string | null;
  created_at: string;
}

/** Active board columns are the positive ladder; terminal statuses collapse into "closed". */
export type JobBoardColumnKey =
  | "saved"
  | "applied"
  | "phone_screen"
  | "interview"
  | "offer"
  | "closed";

export interface JobBoard {
  columns: Record<JobBoardColumnKey, JobApplication[]>;
  counts: Record<string, number>;
  total: number;
}

export interface MeetingPrep {
  id?: string;
  subject: string;
  html: string;
  text: string;
  event_id: string;
  event_title: string | null;
  event_start: string | null;
  cached?: boolean;
  pending?: boolean;
}
