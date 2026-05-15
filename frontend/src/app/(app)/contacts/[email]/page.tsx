"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowLeft,
  Star,
  Mail,
  Tag,
  CheckSquare,
  Square,
  MessageSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Contact, Email } from "@/lib/types";
import {
  RelationshipChart,
  generateSentimentHistory,
} from "@/components/contacts/RelationshipChart";

// ---------------------------------------------------------------------------
// Extended type — API may return more fields than the base Contact
// ---------------------------------------------------------------------------

interface ContactProfile extends Contact {
  avg_response_time?: string | null;
  meetings_count?: number;
  sentiment_history?: { week: string; score: number }[];
}

interface Meeting {
  id: string;
  title: string;
  date: string;
  summary: string | null;
  attendees?: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const AVATAR_PALETTE = [
  "bg-rose-600",
  "bg-orange-600",
  "bg-amber-600",
  "bg-emerald-600",
  "bg-teal-600",
  "bg-cyan-600",
  "bg-blue-600",
  "bg-violet-600",
  "bg-fuchsia-600",
];

function avatarColor(email: string): string {
  let hash = 0;
  for (const ch of email) hash = ((hash * 31) + ch.charCodeAt(0)) & 0xffff;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

function initials(name: string | null, email: string): string {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2)
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const days = Math.floor(diff / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return d.toLocaleDateString("en", { weekday: "short" });
  return d.toLocaleDateString("en", { month: "short", day: "numeric" });
}

function strengthLabel(score: number): { label: string; color: string } {
  if (score >= 0.7) return { label: "Strong", color: "text-emerald-400" };
  if (score >= 0.4) return { label: "Moderate", color: "text-amber-400" };
  return { label: "Weak", color: "text-red-400" };
}

// Seed for chart based on email string
function emailSeed(email: string): number {
  let h = 0;
  for (const ch of email) h = ((h * 31) + ch.charCodeAt(0)) & 0xffffff;
  return h;
}

// ---------------------------------------------------------------------------
// Stat tile
// ---------------------------------------------------------------------------

function StatTile({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-slate-700/50 bg-slate-800/40 px-3 py-2.5">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="font-semibold text-slate-100">{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compact email row
// ---------------------------------------------------------------------------

function EmailRow({ email }: { email: Email }) {
  return (
    <Link
      href={`/inbox/${email.id}`}
      className="flex items-center gap-3 rounded-md px-3 py-2 transition-colors hover:bg-slate-700/50"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-slate-200">
          {email.subject ?? "(no subject)"}
        </p>
        {email.snippet && (
          <p className="truncate text-xs text-slate-500">{email.snippet}</p>
        )}
      </div>
      <span className="shrink-0 text-xs text-slate-500">
        {formatTime(email.received_at)}
      </span>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Meeting card
// ---------------------------------------------------------------------------

function MeetingCard({ meeting }: { meeting: Meeting }) {
  const snippet = meeting.summary
    ? meeting.summary.slice(0, 120) +
      (meeting.summary.length > 120 ? "…" : "")
    : null;

  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <p className="font-medium text-slate-200 truncate">{meeting.title}</p>
        <span className="shrink-0 text-xs text-slate-500">
          {formatDate(meeting.date)}
        </span>
      </div>
      {snippet && (
        <p className="mt-1 text-xs text-slate-500">{snippet}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ContactProfilePage() {
  const params = useParams();
  const router = useRouter();
  const rawEmail = params.email as string;
  const email = decodeURIComponent(rawEmail);

  // ---- Data fetching ----

  const {
    data,
    isLoading,
    error,
    mutate: mutateContact,
  } = useSWR<{
    contact: ContactProfile;
    recent_emails: Email[];
    recent_meetings: Meeting[];
  }>(
    `/contacts/${encodeURIComponent(email)}`,
    (url: string) => api.get<{
      contact: ContactProfile;
      recent_emails: Email[];
      recent_meetings: Meeting[];
    }>(url),
  );

  const contact = data?.contact;

  // ---- VIP toggle (optimistic) ----

  const [localVip, setLocalVip] = useState<boolean | null>(null);
  const [vipPending, setVipPending] = useState(false);

  useEffect(() => {
    if (contact && localVip === null) {
      setLocalVip(contact.vip);
    }
  }, [contact, localVip]);

  const effectiveVip = localVip ?? contact?.vip ?? false;

  async function toggleVip() {
    if (!contact || vipPending) return;
    const next = !effectiveVip;
    setLocalVip(next);
    setVipPending(true);
    try {
      await api.patch(`/contacts/${encodeURIComponent(email)}`, { vip: next });
      mutateContact();
    } catch {
      setLocalVip(!next); // rollback
    } finally {
      setVipPending(false);
    }
  }

  // ---- Chart data ----

  const chartData =
    contact?.sentiment_history ??
    (contact
      ? generateSentimentHistory(
          contact.sentiment_trend,
          contact.relationship_strength,
          emailSeed(email),
        )
      : []);

  // ---- Loading / error ----

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <div className="h-8 w-32 animate-pulse rounded-lg bg-slate-700" />
        <div className="h-28 animate-pulse rounded-xl border border-slate-700/50 bg-slate-800/40" />
        <div className="grid grid-cols-3 gap-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div
              key={i}
              className="h-14 animate-pulse rounded-lg bg-slate-800/40"
            />
          ))}
        </div>
      </div>
    );
  }

  if (error || !contact) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-slate-400">
        <p>Contact not found.</p>
        <button
          onClick={() => router.push("/contacts")}
          className="text-sm text-indigo-400 hover:text-indigo-300"
        >
          ← Back to contacts
        </button>
      </div>
    );
  }

  const bg = avatarColor(email);
  const init = initials(contact.name, email);
  const { label: strengthLbl, color: strengthClr } = strengthLabel(
    contact.relationship_strength,
  );
  const recentEmails = data?.recent_emails ?? [];
  const recentMeetings = (data?.recent_meetings ?? []).slice(0, 3);

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-6 pb-12">
      {/* Back nav */}
      <button
        onClick={() => router.push("/contacts")}
        className="flex w-fit items-center gap-1.5 text-sm text-slate-400 transition-colors hover:text-slate-200"
      >
        <ArrowLeft className="h-4 w-4" />
        Contacts
      </button>

      {/* ---- Header ---- */}
      <div className="flex items-start gap-4">
        <div
          className={`flex h-16 w-16 shrink-0 items-center justify-center rounded-full text-xl font-bold text-white ${bg}`}
        >
          {init}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-xl font-bold text-slate-100">
              {contact.name ?? email}
            </h1>
            <button
              onClick={toggleVip}
              disabled={vipPending}
              title={effectiveVip ? "Remove VIP" : "Mark as VIP"}
              className="shrink-0 transition-opacity disabled:opacity-50"
            >
              <Star
                className={`h-5 w-5 ${
                  effectiveVip
                    ? "fill-amber-400 text-amber-400"
                    : "text-slate-600 hover:text-amber-400"
                }`}
              />
            </button>
          </div>
          {contact.name && (
            <p className="text-sm text-slate-400">{email}</p>
          )}
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-sm text-slate-400">
            {contact.company && <span>{contact.company}</span>}
            {contact.role && (
              <span className="text-slate-500">· {contact.role}</span>
            )}
          </div>
        </div>
      </div>

      {/* ---- Relationship overview ---- */}
      <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4 space-y-4">
        {/* Stat strip */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <div className="flex flex-col gap-0.5 rounded-lg border border-slate-700/50 bg-slate-800/40 px-3 py-2.5">
            <p className="text-xs text-slate-500">Strength</p>
            <p className={`text-lg font-bold ${strengthClr}`}>
              {contact.relationship_strength.toFixed(2)}
              <span className="ml-1.5 text-xs font-normal text-slate-400">
                {strengthLbl}
              </span>
            </p>
          </div>
          <StatTile label="Emails" value={contact.total_emails} />
          {contact.meetings_count !== undefined && (
            <StatTile label="Meetings" value={contact.meetings_count} />
          )}
          <StatTile label="Last contacted" value={formatDate(contact.last_contacted)} />
          {contact.avg_response_time && (
            <StatTile label="Avg response" value={contact.avg_response_time} />
          )}
        </div>

        {/* Sentiment chart */}
        {chartData.length > 0 && (
          <div>
            <p className="mb-1 text-xs text-slate-500">
              Sentiment trend
              {contact.sentiment_trend && (
                <span
                  className={`ml-1.5 font-medium ${
                    contact.sentiment_trend === "improving"
                      ? "text-emerald-400"
                      : contact.sentiment_trend === "deteriorating"
                        ? "text-red-400"
                        : "text-slate-400"
                  }`}
                >
                  {contact.sentiment_trend}
                </span>
              )}
            </p>
            <RelationshipChart data={chartData} trend={contact.sentiment_trend} />
          </div>
        )}
      </div>

      {/* ---- Three-column section ---- */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {/* Topics discussed */}
        <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Tag className="h-4 w-4 text-slate-500" />
            <h2 className="text-sm font-semibold text-slate-200">Topics</h2>
          </div>
          {contact.topics_discussed.length === 0 ? (
            <p className="text-xs text-slate-500">None recorded yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {contact.topics_discussed.map((topic) => (
                <span
                  key={topic}
                  className="rounded-full bg-indigo-600/20 px-2.5 py-1 text-xs font-medium text-indigo-300 ring-1 ring-indigo-500/30"
                >
                  {topic}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Your open commitments */}
        <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
          <div className="mb-3 flex items-center gap-2">
            <CheckSquare className="h-4 w-4 text-slate-500" />
            <h2 className="text-sm font-semibold text-slate-200">
              Your commitments
            </h2>
          </div>
          {contact.open_commitments.length === 0 ? (
            <p className="text-xs text-slate-500">No open commitments.</p>
          ) : (
            <ul className="space-y-2">
              {contact.open_commitments.map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <Square className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
                  <span className="text-slate-300">{item}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Their open commitments */}
        <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Square className="h-4 w-4 text-slate-500" />
            <h2 className="text-sm font-semibold text-slate-200">
              Their commitments
            </h2>
          </div>
          {contact.their_open_commitments.length === 0 ? (
            <p className="text-xs text-slate-500">No open commitments.</p>
          ) : (
            <ul className="space-y-2">
              {contact.their_open_commitments.map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <Square className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
                  <span className="text-slate-300">{item}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* ---- Email history ---- */}
      <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <Mail className="h-4 w-4 text-slate-500" />
          <h2 className="text-sm font-semibold text-slate-200">
            Email history
          </h2>
        </div>
        {recentEmails.length === 0 ? (
          <p className="text-xs text-slate-500">No emails found.</p>
        ) : (
          <div className="divide-y divide-slate-700/50">
            {recentEmails.map((em) => (
              <EmailRow key={em.id} email={em} />
            ))}
          </div>
        )}
      </div>

      {/* ---- Meeting notes ---- */}
      {(recentMeetings.length > 0 || !data) && (
        <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
          <div className="mb-3 flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-slate-500" />
            <h2 className="text-sm font-semibold text-slate-200">
              Meeting notes
            </h2>
          </div>
          {recentMeetings.length === 0 ? (
            <p className="text-xs text-slate-500">No meeting notes yet.</p>
          ) : (
            <div className="space-y-3">
              {recentMeetings.map((m) => (
                <MeetingCard key={m.id} meeting={m} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
