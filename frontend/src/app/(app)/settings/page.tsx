"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { useSWRConfig } from "swr";
import {
  Save,
  Plus,
  X,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  Star,
  LogOut,
  Unlink,
  Link2,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { clearAllSWR } from "@/components/auth/AuthSync";
import type {
  Settings,
  MeetingPrepMode,
  EnergyProfile,
  StyleProfile,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DIGEST_TIME_OPTIONS = [
  "08:00",
  "09:00",
  "12:00",
  "17:00",
  "20:00",
] as const;

// Full IANA timezone list — falls back to common zones on older environments
const TIMEZONES: string[] = (() => {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [
      "UTC",
      "America/New_York",
      "America/Chicago",
      "America/Denver",
      "America/Los_Angeles",
      "America/Anchorage",
      "Pacific/Honolulu",
      "Europe/London",
      "Europe/Paris",
      "Europe/Berlin",
      "Europe/Moscow",
      "Asia/Dubai",
      "Asia/Kolkata",
      "Asia/Singapore",
      "Asia/Tokyo",
      "Asia/Shanghai",
      "Australia/Sydney",
      "Pacific/Auckland",
    ];
  }
})();

const STYLE_GENERATING_MESSAGES = [
  "Analysing your writing style…",
  "Reading your sent emails…",
  "Identifying patterns…",
  "Almost done…",
];

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

interface ToastMsg {
  id: number;
  message: string;
  type: "success" | "error";
}

let toastSeq = 0;

function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastMsg;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const id = setTimeout(onDismiss, 3500);
    return () => clearTimeout(id);
  }, [onDismiss]);

  return (
    <div
      className={`flex items-center gap-2 rounded-lg border px-4 py-3 shadow-xl text-sm ${
        toast.type === "success"
          ? "border-emerald-500/30 bg-slate-800 text-emerald-400"
          : "border-red-500/30 bg-slate-800 text-red-400"
      }`}
    >
      {toast.type === "success" ? (
        <CheckCircle className="h-4 w-4 shrink-0" />
      ) : (
        <AlertCircle className="h-4 w-4 shrink-0" />
      )}
      <span>{toast.message}</span>
      <button
        onClick={onDismiss}
        className="ml-1 text-slate-500 hover:text-slate-300"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timezone searchable combobox
// ---------------------------------------------------------------------------

function TimezoneSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onOutsideClick(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
        setSearch("");
      }
    }
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, []);

  const filtered = search
    ? TIMEZONES.filter((tz) =>
        tz.toLowerCase().includes(search.toLowerCase()),
      ).slice(0, 40)
    : TIMEZONES.slice(0, 40);

  return (
    <div ref={containerRef} className="relative">
      <input
        value={open ? search : value}
        onChange={(e) => {
          setSearch(e.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          setOpen(true);
          setSearch("");
        }}
        placeholder="Search timezone…"
        className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
      />
      {open && (
        <div className="absolute z-20 mt-1 max-h-52 w-full overflow-y-auto rounded-lg border border-slate-600 bg-slate-800 shadow-xl">
          {filtered.length === 0 && (
            <p className="px-3 py-2 text-xs text-slate-500">
              No matching timezones.
            </p>
          )}
          {filtered.map((tz) => (
            <button
              key={tz}
              type="button"
              onClick={() => {
                onChange(tz);
                setOpen(false);
                setSearch("");
              }}
              className={`w-full px-3 py-2 text-left text-sm transition-colors hover:bg-slate-700 ${
                value === tz ? "text-indigo-400" : "text-slate-200"
              }`}
            >
              {tz}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section wrapper
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Divider() {
  return <div className="border-t border-slate-700/50" />;
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:gap-6">
      <div className="w-full shrink-0 sm:w-44">
        <p className="text-sm font-medium text-slate-300">{label}</p>
        {hint && <p className="text-xs text-slate-500">{hint}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Meeting-prep mode options
// ---------------------------------------------------------------------------

const MEETING_PREP_MODES: { value: MeetingPrepMode; label: string; hint: string }[] = [
  { value: "in_app_only", label: "In-app only", hint: "Show prep cards in Felix; no emails." },
  { value: "email_only", label: "Email only", hint: "Send prep emails; nothing in-app." },
  { value: "both", label: "Both", hint: "Email and in-app card." },
  { value: "off", label: "Off", hint: "Disable meeting prep." },
];

interface GoogleStatus {
  connected: boolean;
  google_email?: string;
}

interface VoiceOption {
  id: string;
  label: string;
}

interface VoiceOptionsResponse {
  voices: VoiceOption[];
}

// ---------------------------------------------------------------------------
// Energy-profile helpers (HH:MM-HH:MM windows, comma-separated)
// ---------------------------------------------------------------------------

const WINDOW_RE = /^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/;

function windowsToString(windows: string[] | undefined): string {
  return (windows ?? []).join(", ");
}

// Convert "HH:MM" to minutes-since-midnight for ordering checks.
function hhmmToMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function parseWindows(raw: string): { ok: true; value: string[] } | { ok: false; error: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: [] };
  const parts = trimmed.split(",").map((p) => p.trim()).filter(Boolean);
  for (const p of parts) {
    if (!WINDOW_RE.test(p)) {
      return { ok: false, error: `Invalid window "${p}". Use HH:MM-HH:MM (e.g. 09:00-12:00).` };
    }
    // Windows are same-day intervals on the backend; reject zero-length or
    // wraparound ranges so /free-slots and focus-block creation get usable
    // bounds instead of silently producing nothing.
    const [start, end] = p.split("-");
    if (hhmmToMinutes(end) <= hhmmToMinutes(start)) {
      return {
        ok: false,
        error: `Window "${p}" must end after it starts (same day).`,
      };
    }
  }
  return { ok: true, value: parts };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const router = useRouter();
  const { mutate } = useSWRConfig();

  // ---- Remote data ----
  const {
    data: settings,
    isLoading,
    mutate: mutateSettings,
  } = useSWR<Settings>("/settings", (url: string) =>
    api.get<Settings>(url),
  );

  const { data: googleStatus, mutate: mutateGoogle } =
    useSWR<GoogleStatus>("/auth/google/status", (url: string) =>
      api.get<GoogleStatus>(url),
    );

  const { data: voiceOptionsData, isLoading: loadingVoices } =
    useSWR<VoiceOptionsResponse>("/settings/voices", (url: string) =>
      api.get<VoiceOptionsResponse>(url),
    );

  // ---- Toast ----
  const [toasts, setToasts] = useState<ToastMsg[]>([]);

  const showToast = useCallback(
    (message: string, type: "success" | "error" = "success") => {
      const id = ++toastSeq;
      setToasts((prev) => [...prev, { id, message, type }]);
    },
    [],
  );

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ---- Local state (initialized once from remote) ----
  const initialized = useRef(false);

  const [displayName, setDisplayName] = useState("");
  const [userEmail, setUserEmail] = useState<string | null>(null);

  const [timezone, setTimezone] = useState("UTC");
  const [briefingTime, setBriefingTime] = useState("07:00");
  const [digestMode, setDigestMode] = useState(false);
  const [digestTimes, setDigestTimes] = useState<string[]>([]);

  const [vipContacts, setVipContacts] = useState<string[]>([]);
  const [vipInput, setVipInput] = useState("");
  const [vipError, setVipError] = useState<string | null>(null);

  const [meetingPrepMode, setMeetingPrepMode] =
    useState<MeetingPrepMode>("in_app_only");

  const [deepWorkInput, setDeepWorkInput] = useState("");
  const [meetingsInput, setMeetingsInput] = useState("");
  const [energyError, setEnergyError] = useState<string | null>(null);

  const [felixVoiceId, setFelixVoiceId] = useState("");
  const voiceOptions = voiceOptionsData?.voices ?? [
    { id: "", label: "System default" },
  ];
  const displayedVoiceOptions =
    felixVoiceId && !voiceOptions.some((v) => v.id === felixVoiceId)
      ? [...voiceOptions, { id: felixVoiceId, label: `Current (${felixVoiceId})` }]
      : voiceOptions;

  // Analyse-style state
  const [analysing, setAnalysing] = useState(false);
  const [analyseMessage, setAnalyseMessage] = useState(
    STYLE_GENERATING_MESSAGES[0],
  );
  const analyseIntervalRef = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );

  // Per-section saving
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [savingVip, setSavingVip] = useState(false);
  const [savingMeetingPrep, setSavingMeetingPrep] = useState(false);
  const [savingEnergy, setSavingEnergy] = useState(false);
  const [savingVoice, setSavingVoice] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  // Initialize from fetched settings
  useEffect(() => {
    if (settings && !initialized.current) {
      initialized.current = true;
      setDisplayName(settings.display_name ?? "");
      setTimezone(settings.timezone);
      setBriefingTime(settings.briefing_time);
      setDigestMode(settings.digest_mode);
      setDigestTimes(settings.digest_times);
      setVipContacts(settings.vip_contacts);
      setMeetingPrepMode(settings.meeting_prep_mode ?? "in_app_only");
      setDeepWorkInput(windowsToString(settings.energy_profile?.deep_work));
      setMeetingsInput(windowsToString(settings.energy_profile?.meetings));
      setFelixVoiceId(settings.felix_voice_id ?? "");
    }
  }, [settings]);

  // Get user email from Supabase auth
  useEffect(() => {
    supabase.auth.getUser().then(({ data: { user } }) => {
      setUserEmail(user?.email ?? null);
    });
  }, []);

  // Cleanup analyse interval on unmount
  useEffect(() => {
    return () => {
      if (analyseIntervalRef.current) clearInterval(analyseIntervalRef.current);
    };
  }, []);

  // ---- Save helpers ----

  async function patchSettings(
    partial: Partial<Settings>,
    setSaving: (v: boolean) => void,
  ) {
    setSaving(true);
    try {
      await api.patch("/settings", partial);
      await mutateSettings();
      showToast("Settings saved.");
    } catch (err) {
      showToast(
        err instanceof ApiError ? err.message : "Failed to save settings.",
        "error",
      );
    } finally {
      setSaving(false);
    }
  }

  // ---- Section handlers ----

  function saveProfile() {
    patchSettings({ display_name: displayName.trim() || null }, setSavingProfile);
  }

  function saveSchedule() {
    patchSettings(
      {
        timezone,
        briefing_time: briefingTime,
        digest_mode: digestMode,
        digest_times: digestTimes,
      },
      setSavingSchedule,
    );
  }

  function toggleDigestTime(t: string) {
    setDigestTimes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
    );
  }

  function saveMeetingPrepMode(mode: MeetingPrepMode) {
    setMeetingPrepMode(mode);
    patchSettings({ meeting_prep_mode: mode }, setSavingMeetingPrep);
  }

  function saveEnergyProfile() {
    const dw = parseWindows(deepWorkInput);
    const mt = parseWindows(meetingsInput);
    if (!dw.ok) {
      setEnergyError(dw.error);
      return;
    }
    if (!mt.ok) {
      setEnergyError(mt.error);
      return;
    }
    setEnergyError(null);
    const profile: EnergyProfile =
      dw.value.length === 0 && mt.value.length === 0
        ? {}
        : { deep_work: dw.value, meetings: mt.value };
    patchSettings({ energy_profile: profile }, setSavingEnergy);
  }

  function saveFelixVoiceId() {
    patchSettings(
      { felix_voice_id: felixVoiceId.trim() || null },
      setSavingVoice,
    );
  }

  // VIP contacts — saved immediately on each change
  async function saveVipContacts(updated: string[]) {
    setSavingVip(true);
    try {
      await api.put("/settings/vip-contacts", { vip_contacts: updated });
      await mutateSettings();
    } catch (err) {
      showToast(
        err instanceof ApiError ? err.message : "Failed to update VIP contacts.",
        "error",
      );
    } finally {
      setSavingVip(false);
    }
  }

  function addVip() {
    const email = vipInput.trim().toLowerCase();
    if (!email) return;
    // Basic email validation
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setVipError("Enter a valid email address.");
      return;
    }
    if (vipContacts.includes(email)) {
      setVipError("Already in VIP list.");
      return;
    }
    setVipError(null);
    setVipInput("");
    const updated = [...vipContacts, email];
    setVipContacts(updated);
    saveVipContacts(updated);
  }

  function removeVip(email: string) {
    const updated = vipContacts.filter((e) => e !== email);
    setVipContacts(updated);
    saveVipContacts(updated);
  }

  // Google disconnect
  async function handleDisconnect() {
    setDisconnecting(true);
    try {
      await api.del("/auth/google/disconnect");
      await mutateGoogle();
      showToast("Google account disconnected.");
    } catch (err) {
      showToast(
        err instanceof ApiError ? err.message : "Failed to disconnect.",
        "error",
      );
    } finally {
      setDisconnecting(false);
    }
  }

  // Analyse writing style
  async function handleAnalyseStyle() {
    setAnalysing(true);
    let idx = 0;
    setAnalyseMessage(STYLE_GENERATING_MESSAGES[0]);
    analyseIntervalRef.current = setInterval(() => {
      idx = (idx + 1) % STYLE_GENERATING_MESSAGES.length;
      setAnalyseMessage(STYLE_GENERATING_MESSAGES[idx]);
    }, 3_000);
    try {
      await api.post("/settings/analyse-style");
      await mutateSettings();
      showToast("Writing style updated.");
    } catch (err) {
      showToast(
        err instanceof ApiError ? err.message : "Analysis failed.",
        "error",
      );
    } finally {
      if (analyseIntervalRef.current) {
        clearInterval(analyseIntervalRef.current);
        analyseIntervalRef.current = null;
      }
      setAnalysing(false);
    }
  }

  // Sign out
  async function handleSignOut() {
    await clearAllSWR(mutate);
    await supabase.auth.signOut();
    router.push("/login");
  }

  // ---- Style profile data ----
  const styleProfile: StyleProfile | null = settings?.style_profile ?? null;

  // ---- Render ----

  if (isLoading) {
    return (
      <div className="p-6 space-y-6">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-32 animate-pulse rounded-xl border border-slate-700/50 bg-slate-800/40"
          />
        ))}
      </div>
    );
  }

  return (
    <>
      {/* Toast stack */}
      <div className="fixed right-4 top-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <Toast key={t.id} toast={t} onDismiss={() => dismissToast(t.id)} />
        ))}
      </div>

      <div className="mx-auto max-w-2xl space-y-8 p-6 pb-16">
        <h1 className="text-xl font-semibold text-slate-100">Settings</h1>

        {/* ================================================================
            Section 1 — Profile
        ================================================================ */}
        <Section title="Profile">
          <Field label="Display name" hint="Used in briefings and emails.">
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your name"
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </Field>

          <Field label="Your email" hint="From your Supabase account.">
            <input
              value={userEmail ?? "—"}
              readOnly
              className="w-full rounded-lg border border-slate-700 bg-slate-900/50 px-3 py-2 text-sm text-slate-400 cursor-default select-all"
            />
          </Field>

          <div className="flex justify-end">
            <button
              onClick={saveProfile}
              disabled={savingProfile}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              {savingProfile ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {savingProfile ? "Saving…" : "Save profile"}
            </button>
          </div>
        </Section>

        <Divider />

        {/* ================================================================
            Section 2 — Schedule
        ================================================================ */}
        <Section title="Schedule">
          <Field label="Timezone">
            <TimezoneSelect value={timezone} onChange={setTimezone} />
          </Field>

          <Field
            label="Briefing time"
            hint="When your daily briefing is generated."
          >
            <input
              type="time"
              step={900}
              value={briefingTime}
              onChange={(e) => setBriefingTime(e.target.value)}
              className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none"
            />
          </Field>

          <Field
            label="Digest mode"
            hint="Batch non-urgent emails into scheduled digests."
          >
            <button
              role="switch"
              aria-checked={digestMode}
              onClick={() => setDigestMode((v) => !v)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                digestMode ? "bg-indigo-600" : "bg-slate-700"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                  digestMode ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </Field>

          {digestMode && (
            <Field
              label="Digest times"
              hint="When to deliver your email digest."
            >
              <div className="flex flex-wrap gap-2">
                {DIGEST_TIME_OPTIONS.map((t) => {
                  const active = digestTimes.includes(t);
                  return (
                    <button
                      key={t}
                      onClick={() => toggleDigestTime(t)}
                      className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                        active
                          ? "border-indigo-500 bg-indigo-600/20 text-indigo-300"
                          : "border-slate-600 text-slate-400 hover:border-slate-500 hover:text-slate-200"
                      }`}
                    >
                      {t}
                    </button>
                  );
                })}
              </div>
            </Field>
          )}

          <div className="flex justify-end">
            <button
              onClick={saveSchedule}
              disabled={savingSchedule}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              {savingSchedule ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {savingSchedule ? "Saving…" : "Save schedule"}
            </button>
          </div>
        </Section>

        <Divider />

        {/* ================================================================
            Meeting Prep
        ================================================================ */}
        <Section title="Meeting Prep">
          <p className="text-xs text-slate-500">
            Where Felix delivers pre-meeting prep cards.
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {MEETING_PREP_MODES.map((opt) => {
              const active = meetingPrepMode === opt.value;
              return (
                <button
                  key={opt.value}
                  onClick={() => saveMeetingPrepMode(opt.value)}
                  disabled={savingMeetingPrep}
                  className={`rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50 ${
                    active
                      ? "border-indigo-500 bg-indigo-600/20 text-indigo-200"
                      : "border-slate-600 bg-slate-800/40 text-slate-300 hover:border-slate-500"
                  }`}
                >
                  <p className="font-medium">{opt.label}</p>
                  <p className="text-xs text-slate-500">{opt.hint}</p>
                </button>
              );
            })}
          </div>
        </Section>

        <Divider />

        {/* ================================================================
            Energy Profile
        ================================================================ */}
        <Section title="Energy Profile">
          <p className="text-xs text-slate-500">
            Tells Felix when to protect focus time and when to suggest
            meetings. Use <code className="text-slate-400">HH:MM-HH:MM</code>,
            comma-separated.
          </p>

          <Field
            label="Deep work windows"
            hint="Excluded from /free-slots; used to suggest focus blocks."
          >
            <input
              value={deepWorkInput}
              onChange={(e) => {
                setDeepWorkInput(e.target.value);
                setEnergyError(null);
              }}
              placeholder="09:00-12:00, 14:00-15:30"
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </Field>

          <Field
            label="Meeting windows"
            hint="When Felix is allowed to propose meetings (defaults to 09:00-18:00)."
          >
            <input
              value={meetingsInput}
              onChange={(e) => {
                setMeetingsInput(e.target.value);
                setEnergyError(null);
              }}
              placeholder="13:00-17:00"
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </Field>

          {energyError && (
            <p className="text-xs text-red-400">{energyError}</p>
          )}

          <div className="flex justify-end">
            <button
              onClick={saveEnergyProfile}
              disabled={savingEnergy}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              {savingEnergy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {savingEnergy ? "Saving…" : "Save energy profile"}
            </button>
          </div>
        </Section>

        <Divider />

        {/* ================================================================
            Section 3 — Gmail Connection
        ================================================================ */}
        <Section title="Gmail Connection">
          {googleStatus?.connected ? (
            <div className="flex items-center justify-between gap-4 rounded-lg border border-slate-700/50 bg-slate-800/40 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                <div>
                  <p className="text-sm font-medium text-slate-200">
                    Connected
                  </p>
                  {googleStatus.google_email && (
                    <p className="text-xs text-slate-500">
                      {googleStatus.google_email}
                    </p>
                  )}
                </div>
              </div>
              <button
                onClick={handleDisconnect}
                disabled={disconnecting}
                className="flex items-center gap-1.5 rounded-lg border border-slate-600 px-3 py-1.5 text-sm text-slate-300 transition-colors hover:border-red-500/50 hover:text-red-400 disabled:opacity-50"
              >
                {disconnecting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Unlink className="h-3.5 w-3.5" />
                )}
                {disconnecting ? "Disconnecting…" : "Disconnect"}
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-4 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-amber-500" />
                <p className="text-sm text-amber-300">Not connected</p>
              </div>
              <button
                onClick={() => router.push("/connect")}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
              >
                <Link2 className="h-3.5 w-3.5" />
                Connect Google
              </button>
            </div>
          )}
        </Section>

        <Divider />

        {/* ================================================================
            Section 4 — VIP Contacts
        ================================================================ */}
        <Section title="VIP Contacts">
          <p className="text-xs text-slate-500">
            VIP contacts get priority treatment — their emails always show in
            your inbox, never batched.
          </p>

          {/* Current VIPs */}
          {vipContacts.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {vipContacts.map((email) => (
                <span
                  key={email}
                  className="flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 pl-2.5 pr-1.5 py-1 text-xs text-amber-300"
                >
                  <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                  {email}
                  <button
                    onClick={() => removeVip(email)}
                    disabled={savingVip}
                    className="ml-0.5 rounded-full p-0.5 text-amber-400/70 transition-colors hover:text-amber-300 disabled:opacity-50"
                    aria-label={`Remove ${email} from VIP`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {vipContacts.length === 0 && (
            <p className="text-xs text-slate-500">No VIP contacts added yet.</p>
          )}

          {/* Add VIP */}
          <div className="flex gap-2">
            <div className="flex-1">
              <input
                value={vipInput}
                onChange={(e) => {
                  setVipInput(e.target.value);
                  setVipError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addVip();
                }}
                placeholder="email@example.com"
                className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
              />
              {vipError && (
                <p className="mt-1 text-xs text-red-400">{vipError}</p>
              )}
            </div>
            <button
              onClick={addVip}
              disabled={savingVip || !vipInput.trim()}
              className="flex items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-50"
            >
              {savingVip ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              Add VIP
            </button>
          </div>
        </Section>

        <Divider />

        {/* ================================================================
            Section 5 — Writing Style
        ================================================================ */}
        <Section title="Writing Style">
          <p className="text-xs text-slate-500">
            Felix analyses your sent emails to match your tone when drafting
            replies.
          </p>

          {/* Last analysed + profile summary */}
          {styleProfile ? (
            <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-4 space-y-3">
              {styleProfile.last_analyzed && (
                <p className="text-xs text-slate-500">
                  Last analysed:{" "}
                  {new Date(styleProfile.last_analyzed).toLocaleDateString(
                    "en",
                    { month: "long", day: "numeric", year: "numeric" },
                  )}
                </p>
              )}

              <div className="grid grid-cols-2 gap-3">
                {styleProfile.formality_score !== undefined && (
                  <div>
                    <p className="text-xs text-slate-500 mb-1">
                      Formality score
                    </p>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-700">
                        <div
                          className="h-full rounded-full bg-indigo-500"
                          style={{
                            width: `${Math.min(100, styleProfile.formality_score * 10)}%`,
                          }}
                        />
                      </div>
                      <span className="text-xs font-semibold text-slate-200">
                        {styleProfile.formality_score.toFixed(1)}
                      </span>
                    </div>
                  </div>
                )}

                {styleProfile.avg_word_count !== undefined && (
                  <div>
                    <p className="text-xs text-slate-500">Avg email length</p>
                    <p className="text-sm font-semibold text-slate-200">
                      {styleProfile.avg_word_count} words
                    </p>
                  </div>
                )}
              </div>

              {styleProfile.common_greetings &&
                styleProfile.common_greetings.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs text-slate-500">
                      Common greetings
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {styleProfile.common_greetings.map((g) => (
                        <span
                          key={g}
                          className="rounded-full bg-slate-700/60 px-2.5 py-0.5 text-xs text-slate-300"
                        >
                          {g}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

              {styleProfile.common_sign_offs &&
                styleProfile.common_sign_offs.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs text-slate-500">
                      Common sign-offs
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {styleProfile.common_sign_offs.map((s) => (
                        <span
                          key={s}
                          className="rounded-full bg-slate-700/60 px-2.5 py-0.5 text-xs text-slate-300"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              No style profile yet — run an analysis to get started.
            </p>
          )}

          <button
            onClick={handleAnalyseStyle}
            disabled={analysing}
            className="flex items-center gap-2 rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-60"
          >
            {analysing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {analysing ? analyseMessage : "Re-analyse my writing style"}
          </button>
        </Section>

        <Divider />

        {/* ================================================================
            Voice
        ================================================================ */}
        <Section title="Voice">
          <p className="text-xs text-slate-500">
            Override the ElevenLabs voice used for briefings and the voice
            assistant.
          </p>
          <Field
            label="Felix voice"
            hint="Pick a voice or fall back to the system default."
          >
            <select
              value={felixVoiceId}
              onChange={(e) => setFelixVoiceId(e.target.value)}
              disabled={loadingVoices}
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none"
            >
              {displayedVoiceOptions.map((v) => (
                <option key={v.id || "default"} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex justify-end">
            <button
              onClick={saveFelixVoiceId}
              disabled={savingVoice}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            >
              {savingVoice ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {savingVoice ? "Saving…" : "Save voice"}
            </button>
          </div>
        </Section>

        <Divider />

        {/* ================================================================
            Section 6 — Danger Zone
        ================================================================ */}
        <Section title="Danger Zone">
          <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-200">Sign out</p>
                <p className="text-xs text-slate-500">
                  Sign out of your Felix account on this device.
                </p>
              </div>
              <button
                onClick={handleSignOut}
                className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-sm text-slate-300 transition-colors hover:bg-slate-700"
              >
                <LogOut className="h-3.5 w-3.5" />
                Sign out
              </button>
            </div>

            <div className="border-t border-red-500/10" />

            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-200">
                  Disconnect Google account
                </p>
                <p className="text-xs text-slate-500">
                  Removes Gmail and Calendar access. Data is not deleted.
                </p>
              </div>
              <button
                onClick={handleDisconnect}
                disabled={disconnecting || !googleStatus?.connected}
                className="flex shrink-0 items-center gap-1.5 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm text-red-400 transition-colors hover:bg-red-500/20 disabled:opacity-40"
              >
                {disconnecting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Unlink className="h-3.5 w-3.5" />
                )}
                Disconnect
              </button>
            </div>
          </div>
        </Section>
      </div>
    </>
  );
}
