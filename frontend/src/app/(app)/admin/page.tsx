"use client";

/**
 * Admin dashboard — visible only after the backend confirms admin access.
 * All other users are redirected to /dashboard.
 *
 * Sections:
 *  1. AI Performance table   — GET /eval/feedback/summary
 *  2. Parse Error log        — GET /admin/parse-errors
 *  3. Prompt Versions table  — GET /admin/prompt-versions
 *  4. User Feedback summary  — derived from /eval/feedback/summary, worst-first
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SummaryRow {
  feature: string;
  calls_7d: number;
  avg_latency_ms: number;
  success_pct: number;
  parse_error_pct: number;
  avg_user_rating: number | null;
  rated_count: number;
  good_count: number;
  edited_count: number;
  wrong_count: number;
}

interface ParseError {
  id: string;
  feature: string;
  prompt_version: string | null;
  error_message: string | null;
  created_at: string;
}

interface PromptVersionRow {
  prompt_version: string;
  feature: string;
  call_count: number;
  avg_latency_ms: number;
  success_pct: number;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-5">
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-slate-500">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="pb-2 pr-4 text-left text-xs font-medium text-slate-500">
      {children}
    </th>
  );
}

function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <td className={`py-2 pr-4 text-sm text-slate-300 ${className}`}>
      {children}
    </td>
  );
}

function EmptyRow({ cols, label }: { cols: number; label: string }) {
  return (
    <tr>
      <td colSpan={cols} className="py-5 text-center text-sm text-slate-600">
        {label}
      </td>
    </tr>
  );
}

function SuccessBadge({ pct }: { pct: number }) {
  const cls =
    pct > 95
      ? "bg-emerald-500/20 text-emerald-400"
      : pct >= 85
        ? "bg-yellow-500/20 text-yellow-400"
        : "bg-red-500/20 text-red-400";
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${cls}`}>
      {pct.toFixed(1)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const router = useRouter();
  const [authorized, setAuthorized] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  const [summary, setSummary] = useState<SummaryRow[]>([]);
  const [parseErrors, setParseErrors] = useState<ParseError[]>([]);
  const [promptVersions, setPromptVersions] = useState<PromptVersionRow[]>([]);

  // ── Auth check ─────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    api
      .get<{ admin: boolean }>("/admin/me", { skipAuthRedirect: true })
      .then(() => {
        if (!cancelled) {
          setAuthorized(true);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setAuthorized(false);
        setLoading(false);
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        } else {
          router.replace("/dashboard");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  // ── Fetch data once authorised ─────────────────────────────────────────────
  useEffect(() => {
    if (!authorized) return;
    Promise.all([
      api.get<SummaryRow[]>("/eval/feedback/summary"),
      api.get<ParseError[]>("/admin/parse-errors"),
      api.get<PromptVersionRow[]>("/admin/prompt-versions"),
    ])
      .then(([s, pe, pv]) => {
        setSummary(s ?? []);
        setParseErrors(pe ?? []);
        setPromptVersions(pv ?? []);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) {
          setAuthorized(false);
          router.replace("/dashboard");
        }
      })
      .finally(() => setLoading(false));
  }, [authorized, router]);

  // Still verifying access, or redirecting after denial.
  if (authorized !== true) return null;

  // Feedback summary sorted by Wrong % descending (worst performers first)
  const feedbackRows = [...summary]
    .filter((r) => r.rated_count > 0)
    .sort(
      (a, b) =>
        b.wrong_count / Math.max(b.rated_count, 1) -
        a.wrong_count / Math.max(a.rated_count, 1),
    );

  return (
    <div className="space-y-6 pb-12">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-slate-100">Admin Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">
          AI performance, parse errors, prompt versions, and user feedback
        </p>
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center text-sm text-slate-600">
          Loading…
        </div>
      ) : (
        <>
          {/* ── 1. AI Performance ─────────────────────────────────────────── */}
          <Section title="AI Performance — last 7 days">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <Th>Feature</Th>
                    <Th>Calls (7d)</Th>
                    <Th>Avg Latency</Th>
                    <Th>Success %</Th>
                    <Th>Parse Error %</Th>
                    <Th>User Rating</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/40">
                  {summary.length === 0 ? (
                    <EmptyRow cols={6} label="No data yet" />
                  ) : (
                    summary.map((row) => (
                      <tr key={row.feature}>
                        <Td className="font-medium capitalize">
                          {row.feature.replace(/_/g, " ")}
                        </Td>
                        <Td>{row.calls_7d.toLocaleString()}</Td>
                        <Td>{row.avg_latency_ms}ms</Td>
                        <Td>
                          <SuccessBadge pct={row.success_pct} />
                        </Td>
                        <Td className="text-slate-400">
                          {row.parse_error_pct.toFixed(1)}%
                        </Td>
                        <Td>
                          {row.avg_user_rating != null
                            ? row.avg_user_rating.toFixed(2)
                            : "—"}
                        </Td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </Section>

          {/* ── 2. Parse Error Log ────────────────────────────────────────── */}
          <Section title="Parse Error Log — latest 20">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <Th>Feature</Th>
                    <Th>Prompt Version</Th>
                    <Th>Error</Th>
                    <Th>Time</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/40">
                  {parseErrors.length === 0 ? (
                    <EmptyRow cols={4} label="No parse errors" />
                  ) : (
                    parseErrors.map((err) => (
                      <tr key={err.id}>
                        <Td className="capitalize">
                          {err.feature.replace(/_/g, " ")}
                        </Td>
                        <Td className="font-mono text-xs text-slate-400">
                          {err.prompt_version ?? "—"}
                        </Td>
                        <Td className="max-w-xs truncate text-xs text-red-400">
                          {err.error_message ?? "—"}
                        </Td>
                        <Td className="whitespace-nowrap text-xs text-slate-500">
                          {new Date(err.created_at).toLocaleString()}
                        </Td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </Section>

          {/* ── 3. Prompt Versions ────────────────────────────────────────── */}
          <Section title="Prompt Version Performance">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <Th>Version</Th>
                    <Th>Feature</Th>
                    <Th>Calls</Th>
                    <Th>Avg Latency</Th>
                    <Th>Success %</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/40">
                  {promptVersions.length === 0 ? (
                    <EmptyRow cols={5} label="No data yet" />
                  ) : (
                    promptVersions.map((row, i) => (
                      <tr key={`${row.prompt_version}-${row.feature}-${i}`}>
                        <Td className="font-mono text-xs">
                          {row.prompt_version}
                        </Td>
                        <Td className="capitalize">
                          {row.feature.replace(/_/g, " ")}
                        </Td>
                        <Td>{row.call_count.toLocaleString()}</Td>
                        <Td>{row.avg_latency_ms}ms</Td>
                        <Td>
                          <SuccessBadge pct={row.success_pct} />
                        </Td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </Section>

          {/* ── 4. User Feedback Summary ──────────────────────────────────── */}
          <Section title="User Feedback Summary — worst performers first">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <Th>Feature</Th>
                    <Th>Total Ratings</Th>
                    <Th>Good %</Th>
                    <Th>Edited %</Th>
                    <Th>Wrong %</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/40">
                  {feedbackRows.length === 0 ? (
                    <EmptyRow cols={5} label="No user feedback yet" />
                  ) : (
                    feedbackRows.map((row) => {
                      const n = row.rated_count;
                      const goodPct = (row.good_count / n) * 100;
                      const editedPct = (row.edited_count / n) * 100;
                      const wrongPct = (row.wrong_count / n) * 100;
                      return (
                        <tr key={row.feature}>
                          <Td className="font-medium capitalize">
                            {row.feature.replace(/_/g, " ")}
                          </Td>
                          <Td>{n}</Td>
                          <Td className="text-emerald-400">
                            {goodPct.toFixed(1)}%
                          </Td>
                          <Td className="text-yellow-400">
                            {editedPct.toFixed(1)}%
                          </Td>
                          <Td className="text-red-400">
                            {wrongPct.toFixed(1)}%
                          </Td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
