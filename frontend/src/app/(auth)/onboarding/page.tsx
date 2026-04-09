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
      router.replace("/home");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
      setLoading(false);
    }
  }

  async function handleSkip() {
    router.replace("/home");
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-[#0f172a] px-6 py-12">
      {/* Ambient glow */}
      <div className="pointer-events-none fixed bottom-0 left-1/2 -translate-x-1/2 w-[700px] h-[400px] rounded-full bg-indigo-600/8 blur-3xl" />

      <div className="relative z-10 w-full max-w-md">
        {/* Header */}
        <div className="flex items-center gap-3 mb-8">
          <img
            src="/icon-512.png"
            alt="Felix icon"
            width={36}
            height={36}
            className="rounded-xl shadow-md shadow-indigo-500/20 flex-shrink-0"
          />
          <span className="text-lg font-bold text-[#f1f5f9] tracking-tight">
            Felix
          </span>
        </div>

        <div className="rounded-2xl bg-[#1e293b] border border-white/[0.06] shadow-2xl px-8 py-10">
          {/* Step icon */}
          <div className="mb-6 w-12 h-12 rounded-xl bg-indigo-600/15 border border-indigo-500/25 flex items-center justify-center">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="#818cf8"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
              width={22}
              height={22}
              aria-hidden
            >
              <circle cx="12" cy="8" r="4" />
              <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" />
            </svg>
          </div>

          <h1 className="text-xl font-bold text-[#f1f5f9] mb-1">
            Set up your preferences
          </h1>
          <p className="text-sm text-[#94a3b8] mb-8 leading-relaxed">
            Felix uses these to personalise your briefings.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-6">
            {/* Display name */}
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="display_name"
                className="text-sm font-medium text-[#e2e8f0]"
              >
                Your name
              </label>
              <input
                id="display_name"
                type="text"
                placeholder="e.g. Alex"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="rounded-lg bg-[#0f172a] border border-white/[0.08] hover:border-white/[0.14] px-3 py-2.5 text-sm text-[#f1f5f9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-indigo-500/60 focus:border-indigo-500/60 transition-colors"
              />
            </div>

            {/* Timezone */}
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="tz_search"
                className="text-sm font-medium text-[#e2e8f0]"
              >
                Timezone
              </label>
              <input
                id="tz_search"
                type="text"
                placeholder="Search timezone…"
                value={tzSearch}
                onChange={(e) => setTzSearch(e.target.value)}
                className="rounded-lg bg-[#0f172a] border border-white/[0.08] hover:border-white/[0.14] px-3 py-2.5 text-sm text-[#f1f5f9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-indigo-500/60 focus:border-indigo-500/60 transition-colors"
              />
              <select
                id="timezone"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                size={5}
                className="rounded-lg bg-[#0f172a] border border-white/[0.08] px-3 py-1.5 text-sm text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-indigo-500/60 focus:border-indigo-500/60 transition-colors"
              >
                {filteredTz.map((tz) => (
                  <option key={tz} value={tz} className="bg-[#0f172a]">
                    {tz}
                  </option>
                ))}
              </select>
              <p className="text-xs text-[#475569]">Selected: {timezone}</p>
            </div>

            {/* Briefing time */}
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="briefing_time"
                className="text-sm font-medium text-[#e2e8f0]"
              >
                Daily briefing time
              </label>
              <input
                id="briefing_time"
                type="time"
                value={briefingTime}
                onChange={(e) => setBriefingTime(e.target.value)}
                className="rounded-lg bg-[#0f172a] border border-white/[0.08] hover:border-white/[0.14] px-3 py-2.5 text-sm text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-indigo-500/60 focus:border-indigo-500/60 transition-colors w-36"
              />
              <p className="text-xs text-[#475569]">
                Felix will generate your morning briefing at this time.
              </p>
            </div>

            {error && (
              <p className="rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
                {error}
              </p>
            )}

            <div className="flex flex-col gap-3 pt-1">
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 disabled:cursor-wait transition-all duration-150 px-5 py-3.5 text-sm font-semibold text-white shadow-lg shadow-indigo-600/25 hover:shadow-indigo-500/30 hover:-translate-y-0.5 active:translate-y-0"
              >
                {loading ? "Saving…" : "Save & continue"}
              </button>
              <button
                type="button"
                onClick={handleSkip}
                className="w-full rounded-xl border border-white/[0.08] hover:border-white/[0.16] px-5 py-2.5 text-sm text-[#64748b] hover:text-[#94a3b8] transition-colors"
              >
                Skip for now
              </button>
            </div>
          </form>
        </div>
      </div>
    </main>
  );
}
