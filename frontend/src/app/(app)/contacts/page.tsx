"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Search, Star } from "lucide-react";
import { api } from "@/lib/api";
import type { Contact } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type SortKey = "strength" | "last_contacted" | "alpha";

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

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function strengthColor(score: number): string {
  if (score >= 0.7) return "bg-emerald-500";
  if (score >= 0.4) return "bg-amber-500";
  return "bg-red-500";
}

// ---------------------------------------------------------------------------
// Contact card
// ---------------------------------------------------------------------------

function ContactCard({ contact }: { contact: Contact }) {
  const bg = avatarColor(contact.email);
  const init = initials(contact.name, contact.email);
  const pct = Math.round(contact.relationship_strength * 100);
  const bar = strengthColor(contact.relationship_strength);

  return (
    <Link
      href={`/contacts/${encodeURIComponent(contact.email)}`}
      className="group flex flex-col gap-3 rounded-lg border border-slate-700/50 bg-slate-800/40 p-4 transition-colors hover:bg-slate-800/80"
    >
      {/* Avatar + VIP star */}
      <div className="flex items-start justify-between">
        <div
          className={`flex h-12 w-12 items-center justify-center rounded-full text-sm font-bold text-white ${bg}`}
        >
          {init}
        </div>
        {contact.vip && (
          <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
        )}
      </div>

      {/* Name / email / company */}
      <div className="min-w-0">
        <p className="truncate font-semibold text-slate-100">
          {contact.name ?? contact.email}
        </p>
        {contact.name && (
          <p className="truncate text-xs text-slate-500">{contact.email}</p>
        )}
        {contact.company && (
          <p className="truncate text-xs text-slate-400 mt-0.5">
            {contact.company}
          </p>
        )}
      </div>

      {/* Relationship strength bar */}
      <div className="space-y-1">
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500">Relationship</span>
          <span className="font-medium text-slate-300">{pct}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
          <div
            className={`h-full rounded-full transition-all ${bar}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Last contacted */}
      <p className="text-xs text-slate-500">
        Last: {formatDate(contact.last_contacted)}
      </p>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ContactsPage() {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("strength");

  const { data, isLoading, error } = useSWR<{ contacts: Contact[]; count: number }>(
    "/contacts",
    (url: string) => api.get<{ contacts: Contact[]; count: number }>(url),
  );

  const contacts = useMemo(() => {
    let list = data?.contacts ?? [];

    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (c) =>
          c.email.toLowerCase().includes(q) ||
          (c.name?.toLowerCase().includes(q) ?? false),
      );
    }

    return [...list].sort((a, b) => {
      if (sort === "strength")
        return b.relationship_strength - a.relationship_strength;
      if (sort === "last_contacted") {
        const at = a.last_contacted ? new Date(a.last_contacted).getTime() : 0;
        const bt = b.last_contacted ? new Date(b.last_contacted).getTime() : 0;
        return bt - at;
      }
      // alpha
      return (a.name ?? a.email)
        .toLowerCase()
        .localeCompare((b.name ?? b.email).toLowerCase());
    });
  }, [data, search, sort]);

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Contacts</h1>
        {data && (
          <span className="text-sm text-slate-500">
            {data.count} contact{data.count !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Search + sort toolbar */}
      <div className="flex gap-3">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or email…"
            className="w-full rounded-lg border border-slate-600 bg-slate-800 py-2 pl-9 pr-3 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
        </div>

        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-300 focus:border-indigo-500 focus:outline-none"
        >
          <option value="strength">Relationship Strength</option>
          <option value="last_contacted">Last Contacted</option>
          <option value="alpha">Alphabetical</option>
        </select>
      </div>

      {/* Loading skeletons */}
      {isLoading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className="h-44 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
              style={{ animationDelay: `${i * 50}ms` }}
            />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <p className="text-sm text-red-400">
          Failed to load contacts: {(error as Error).message}
        </p>
      )}

      {/* Empty */}
      {!isLoading && !error && contacts.length === 0 && (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-500">
          {search ? "No contacts match your search." : "No contacts yet."}
        </div>
      )}

      {/* Grid */}
      {!isLoading && !error && contacts.length > 0 && (
        <div className="grid grid-cols-2 gap-3 pb-6 sm:grid-cols-3 lg:grid-cols-4">
          {contacts.map((c) => (
            <ContactCard key={c.email} contact={c} />
          ))}
        </div>
      )}
    </div>
  );
}
