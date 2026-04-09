"use client";

import Link from "next/link";
import useSWR from "swr";
import { TrendingUp, TrendingDown, Minus, User } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Contact } from "@/lib/types";

interface ContactSidebarProps {
  senderEmail: string;
}

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function RelationshipBar({ strength }: { strength: number }) {
  const pct = Math.round(strength * 100);
  const color =
    strength >= 0.7
      ? "bg-emerald-500"
      : strength >= 0.4
        ? "bg-yellow-500"
        : "bg-red-500";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400">Relationship strength</span>
        <span className="font-medium text-slate-200">{pct}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function SentimentIcon({ trend }: { trend: string | null }) {
  if (trend === "improving")
    return <TrendingUp className="h-4 w-4 text-emerald-400" />;
  if (trend === "declining")
    return <TrendingDown className="h-4 w-4 text-red-400" />;
  return <Minus className="h-4 w-4 text-slate-400" />;
}

function SkeletonRow({ w = "full" }: { w?: string }) {
  const widths: Record<string, string> = {
    "1/2": "w-1/2",
    "2/3": "w-2/3",
    "3/4": "w-3/4",
    full: "w-full",
  };
  return (
    <div
      className={`h-3 animate-pulse rounded bg-slate-700 ${widths[w] ?? "w-full"}`}
    />
  );
}

function CommitmentList({
  items,
  label,
}: {
  items: string[];
  label: string;
}) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-slate-400">{label}</p>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-1.5 text-xs text-slate-300">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-500" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ContactSidebar({ senderEmail }: ContactSidebarProps) {
  const encodedEmail = encodeURIComponent(senderEmail);
  const { data: contact, isLoading, error } = useSWR<Contact | null>(
    `contact-${senderEmail}`,
    async () => {
      try {
        // Backend returns { contact, recent_emails, recent_meetings } — unwrap.
        const res = await api.get<{ contact: Contact } | Contact>(
          `/contacts/${encodedEmail}`,
        );
        if (res && typeof res === "object" && "contact" in res) {
          return (res as { contact: Contact }).contact ?? null;
        }
        return (res as Contact) ?? null;
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
  );

  // ── Loading skeleton ───────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 animate-pulse rounded-full bg-slate-700" />
          <div className="flex-1 space-y-1.5">
            <SkeletonRow w="2/3" />
            <SkeletonRow w="1/2" />
          </div>
        </div>
        <SkeletonRow />
        <SkeletonRow w="3/4" />
        <SkeletonRow w="1/2" />
      </div>
    );
  }

  // ── Error ──────────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-5">
        <p className="text-xs text-slate-500">Could not load contact info.</p>
      </div>
    );
  }

  // ── Not found — minimal card ───────────────────────────────────────────────
  if (!contact) {
    return (
      <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-700">
            <User className="h-5 w-5 text-slate-400" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-200">{senderEmail}</p>
            <span className="text-xs text-slate-500">New contact</span>
          </div>
        </div>
      </div>
    );
  }

  // ── Full contact card ──────────────────────────────────────────────────────
  const displayName = contact.name ?? contact.email ?? senderEmail ?? "";
  const initial = displayName?.slice(0, 1).toUpperCase() ?? "?";
  const openCommitments = contact.open_commitments ?? [];
  const theirOpenCommitments = contact.their_open_commitments ?? [];

  return (
    <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-5 space-y-4">
      {/* Avatar + name */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold text-white">
          {initial}
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-100">
            {displayName}
          </p>
          {contact.role || contact.company ? (
            <p className="truncate text-xs text-slate-400">
              {[contact.role, contact.company].filter(Boolean).join(" · ")}
            </p>
          ) : (
            <p className="truncate text-xs text-slate-500">{contact.email}</p>
          )}
        </div>
      </div>

      {/* Relationship strength */}
      <RelationshipBar strength={contact.relationship_strength} />

      <hr className="border-slate-700/50" />

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <p className="text-slate-500">Emails exchanged</p>
          <p className="mt-0.5 font-medium text-slate-200">
            {(contact.total_emails ?? 0).toLocaleString()}
          </p>
        </div>
        <div>
          <p className="text-slate-500">Last contacted</p>
          <p className="mt-0.5 font-medium text-slate-200">
            {formatDate(contact.last_contacted)}
          </p>
        </div>
      </div>

      {/* Sentiment trend */}
      {contact.sentiment_trend && (
        <div className="flex items-center gap-1.5 text-xs">
          <SentimentIcon trend={contact.sentiment_trend} />
          <span className="capitalize text-slate-300">
            {contact.sentiment_trend}
          </span>
          <span className="text-slate-500">sentiment</span>
        </div>
      )}

      {/* Commitments */}
      {(openCommitments.length > 0 || theirOpenCommitments.length > 0) && (
        <>
          <hr className="border-slate-700/50" />
          <CommitmentList
            items={openCommitments}
            label="Your open commitments"
          />
          <CommitmentList
            items={theirOpenCommitments}
            label="Their open commitments"
          />
        </>
      )}

      {/* View full profile */}
      <Link
        href={`/contacts/${encodeURIComponent(contact.email)}`}
        className="block text-center text-xs font-medium text-indigo-400 transition-colors hover:text-indigo-300"
      >
        View full profile →
      </Link>
    </div>
  );
}
