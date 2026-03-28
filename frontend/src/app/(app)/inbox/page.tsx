"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { EmailList } from "@/components/inbox/EmailList";
import { useEmails } from "@/hooks/useEmails";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

const TABS = [
  { key: "action_required", label: "Action Required" },
  { key: "fyi", label: "FYI" },
  { key: "waiting_on", label: "Waiting On" },
  { key: "newsletter", label: "Newsletter" },
  { key: "automated", label: "Automated" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

// Lightweight hook just to get the count for each tab badge
function useTabCount(category: string) {
  const { total } = useEmails({ category, limit: 1 });
  return total;
}

function TabButton({
  label,
  category,
  active,
  onClick,
}: {
  label: string;
  category: string;
  active: boolean;
  onClick: () => void;
}) {
  const count = useTabCount(category);

  return (
    <button
      onClick={onClick}
      className={[
        "flex items-center gap-1.5 whitespace-nowrap border-b-2 px-1 pb-3 pt-1 text-sm font-medium transition-colors",
        active
          ? "border-indigo-500 text-indigo-400"
          : "border-transparent text-slate-400 hover:border-slate-600 hover:text-slate-200",
      ].join(" ")}
    >
      {label}
      {count > 0 && (
        <span
          className={`rounded-full px-1.5 py-0.5 text-xs leading-none ${
            active
              ? "bg-indigo-600 text-white"
              : "bg-slate-700 text-slate-300"
          }`}
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function InboxPage() {
  const [activeTab, setActiveTab] = useState<TabKey>("action_required");
  const [search, setSearch] = useState("");

  return (
    <div className="flex h-full flex-col gap-4">
      {/* Search bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by subject or sender…"
          className="w-full rounded-lg border border-slate-700 bg-slate-800 py-2 pl-9 pr-4 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>

      {/* Tab bar */}
      <div className="flex gap-5 border-b border-slate-700 overflow-x-auto">
        {TABS.map(({ key, label }) => (
          <TabButton
            key={key}
            label={label}
            category={key}
            active={activeTab === key}
            onClick={() => {
              setActiveTab(key);
              setSearch("");
            }}
          />
        ))}
      </div>

      {/* Email list */}
      <div className="flex-1 overflow-y-auto pb-4">
        <EmailList category={activeTab} search={search} />
      </div>
    </div>
  );
}
