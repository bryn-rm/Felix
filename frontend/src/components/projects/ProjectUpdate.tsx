"use client";

import { useRef, useState } from "react";
import { useProjectUpdate, useProjectActions, type UpdateClaim } from "@/hooks/useProjects";
import { projectButton } from "./ProjectDialog";
import { CitationPreview } from "./CitationPreview";

const timeLabels = {
  current_context: "Current context",
  source_event_this_week: "Source event this week",
  project_action_this_week: "Project action this week",
};

export function ProjectUpdatePanel({ projectId, navigate, openThread }: {
  projectId: string; navigate: (section: string, recordId?: string | null) => void; openThread: (id: string) => void;
}) {
  const { data, error, isLoading, mutate } = useProjectUpdate(projectId);
  const { generateUpdate } = useProjectActions();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [citation, setCitation] = useState<UpdateClaim["citations"][number] | null>(null);
  // Reuse after transport failure: the server may already have saved this attempt.
  const requestId = useRef<string | null>(null);
  const inFlight = useRef(false);
  const update = data?.update;
  return <div className="space-y-4 rounded-lg border border-indigo-800/60 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold text-slate-100">What changed this week?</h2><button className={projectButton} disabled={busy} onClick={async () => {
      if (inFlight.current) return;
      inFlight.current = true; setBusy(true); setFailure("");
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        requestId.current ??= crypto.randomUUID();
        const controller = new AbortController();
        timer = setTimeout(() => controller.abort(), 75_000);
        await generateUpdate(projectId, requestId.current, controller.signal); requestId.current = null;
      }
      catch (e) { setFailure(e instanceof Error ? e.message : "Could not generate update. Please retry."); }
      finally { clearTimeout(timer); inFlight.current = false; setBusy(false); }
    }}>{busy ? "Generating…" : update || data?.withheld ? "Regenerate update" : "Generate update"}</button></div>
    <p className="text-xs text-slate-400">Generated from this project’s confirmed records and linked evidence. Your confirmed scope and records change only when you edit them.</p>
    {failure && <p role="alert" className="text-sm text-red-400">{failure}</p>}
    {isLoading && <p role="status">Loading project update…</p>}
    {error && <p role="alert">Could not verify the saved update. <button className="underline" onClick={() => void mutate()}>Retry</button></p>}
    {!error && data?.withheld && <p role="status" className="text-sm text-amber-300">The saved update is withheld because supporting evidence is no longer available. Generate again using current evidence.</p>}
    {!error && data?.stale && !data.withheld && <p role="status" className="text-sm text-amber-300">This update is stale. Project evidence or the reporting week has changed. Regenerate to see current information.</p>}
    {!error && update && <>
      <p className="text-xs text-slate-400">Generated {new Date(update.generated_at).toLocaleString(undefined, { timeZone: update.timezone })} · {update.timezone} · Week of {new Date(update.week_start).toLocaleDateString(undefined, { timeZone: update.timezone })}</p>
      {update.claims.length === 0 && <p className="text-sm text-slate-300">There was not enough supported evidence to produce an update. This does not mean nothing changed.</p>}
      <ol className="space-y-4">{update.claims.map((claim, index) => <li key={index} className="space-y-1">
        <p className="text-xs capitalize text-slate-400">{claim.section.replaceAll("_", " ")} · {timeLabels[claim.time_basis]}</p>
        <p className="whitespace-pre-wrap break-words text-sm text-slate-200">{claim.text}</p>
        <div className="flex flex-wrap gap-3">{claim.citations.map((c, i) => <button key={i} className="text-xs text-indigo-400 underline" onClick={() => setCitation(c)}>Evidence {index + 1}.{i + 1}</button>)}</div>
      </li>)}</ol>
    </>}
    {!error && data && !update && !data.withheld && <p className="text-sm text-slate-400">Generate an update when you want to review this week. Nothing is generated automatically.</p>}
    {citation && <CitationPreview projectId={projectId} citation={citation} onClose={() => setCitation(null)} navigate={navigate} openThread={openThread} />}
  </div>;
}
