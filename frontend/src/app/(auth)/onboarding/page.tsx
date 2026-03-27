"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

// IANA timezone list — representative set covering all UTC offsets
const TIMEZONES = Intl.supportedValuesOf
  ? Intl.supportedValuesOf("timeZone")
  : [
      "UTC",
      "America/New_York",
      "America/Chicago",
      "America/Denver",
      "America/Los_Angeles",
      "America/Anchorage",
      "America/Honolulu",
      "Europe/London",
      "Europe/Paris",
      "Europe/Berlin",
      "Europe/Moscow",
      "Asia/Dubai",
      "Asia/Kolkata",
      "Asia/Singapore",
      "Asia/Tokyo",
      "Australia/Sydney",
      "Pacific/Auckland",
    ];

export default function OnboardingPage() {
  const router = useRouter();
  const [displayName, setDisplayName] = useState("");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [tzSearch, setTzSearch] = useState("");
  const [briefingTime, setBriefingTime] = useState("07:30");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const filteredTz = TIMEZONES.filter((tz) =>
    tz.toLowerCase().includes(tzSearch.toLowerCase()),
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.patch("/settings/", {
        display_name: displayName || null,
        timezone,
        briefing_time: briefingTime,
      });
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
      setLoading(false);
    }
  }

  async function handleSkip() {
    router.replace("/dashboard");
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-[#0f172a] px-4">
      <div className="w-full max-w-md rounded-2xl bg-[#1e293b] shadow-2xl px-8 py-10">
        <h1 className="text-xl font-bold text-[#f1f5f9] mb-1">
          Set up your preferences
        </h1>
        <p className="text-sm text-[#94a3b8] mb-8">
          Felix uses these to personalise your briefings.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-6">
          {/* Display name */}
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="display_name"
              className="text-sm font-medium text-[#f1f5f9]"
            >
              Your name
            </label>
            <input
              id="display_name"
              type="text"
              placeholder="e.g. Alex"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="rounded-lg bg-[#0f172a] border border-white/10 px-3 py-2.5 text-sm text-[#f1f5f9] placeholder:text-[#94a3b8] focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Timezone */}
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="tz_search"
              className="text-sm font-medium text-[#f1f5f9]"
            >
              Timezone
            </label>
            <input
              id="tz_search"
              type="text"
              placeholder="Search timezone…"
              value={tzSearch}
              onChange={(e) => setTzSearch(e.target.value)}
              className="rounded-lg bg-[#0f172a] border border-white/10 px-3 py-2.5 text-sm text-[#f1f5f9] placeholder:text-[#94a3b8] focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <select
              id="timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              size={5}
              className="rounded-lg bg-[#0f172a] border border-white/10 px-3 py-1.5 text-sm text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              {filteredTz.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
            <p className="text-xs text-[#94a3b8]">Selected: {timezone}</p>
          </div>

          {/* Briefing time */}
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="briefing_time"
              className="text-sm font-medium text-[#f1f5f9]"
            >
              Daily briefing time
            </label>
            <input
              id="briefing_time"
              type="time"
              value={briefingTime}
              onChange={(e) => setBriefingTime(e.target.value)}
              className="rounded-lg bg-[#0f172a] border border-white/10 px-3 py-2.5 text-sm text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-indigo-500 w-36"
            />
            <p className="text-xs text-[#94a3b8]">
              Felix will generate your morning briefing at this time.
            </p>
          </div>

          {error && (
            <p className="rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
              {error}
            </p>
          )}

          <div className="flex flex-col gap-3 pt-2">
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 disabled:cursor-wait transition-colors px-5 py-3 text-sm font-semibold text-white"
            >
              {loading ? "Saving…" : "Save & continue"}
            </button>
            <button
              type="button"
              onClick={handleSkip}
              className="w-full rounded-xl border border-white/10 hover:border-white/20 px-5 py-2.5 text-sm text-[#94a3b8] hover:text-[#f1f5f9] transition-colors"
            >
              Skip for now
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
