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

export interface Contact {
  email: string;
  name: string | null;
  company: string | null;
  role: string | null;
  vip: boolean;
  relationship_strength: number;
  total_emails: number;
  last_contacted: string | null;
  sentiment_trend: string | null;
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

export interface Settings {
  display_name: string | null;
  timezone: string;
  briefing_time: string;
  digest_mode: boolean;
  digest_times: string[];
  vip_contacts: string[];
  style_profile: Record<string, unknown> | null;
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
