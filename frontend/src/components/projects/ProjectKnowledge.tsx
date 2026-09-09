"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useProjectKnowledge, useProjectActions, useMeetingDecisions, type ProjectScope, type ProjectSource, type ProjectRecord, type RecordKind, type MeetingDecision } from "@/hooks/useProjects";
import { ProjectDialog, projectButton, projectInput } from "./ProjectDialog";
import { ProjectRecordForm } from "./ProjectRecordForm";

function ScopeEditor({ projectId, scope, onClose }: { projectId: string; scope: ProjectScope | null; onClose: () => void }) {
  const [content, setContent] = useState(scope?.content ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { saveScope } = useProjectActions();
  return <ProjectDialog title="Edit confirmed scope" onClose={() => { if (!busy) onClose(); }}>
    <form className="space-y-3" onSubmit={async (e) => {
      e.preventDefault(); setBusy(true); setError("");
      try { await saveScope(projectId, content, scope?.version ?? 0); onClose(); }
      catch (e) { setError(e instanceof Error ? e.message : "Could not save scope."); }
      finally { setBusy(false); }
    }}>
      <label className="block text-sm">Confirmed scope<textarea rows={8} maxLength={10000} className={projectInput} value={content} onChange={(e) => setContent(e.target.value)} /></label>
      <p className="text-xs text-slate-400">Only your edits change confirmed scope. Saving keeps the previous revision.</p>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <button disabled={busy} className={projectButton}>{busy ? "Saving…" : "Save confirmed scope"}</button>
    </form>
  </ProjectDialog>;
}

function ImportMeetingDecision({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const { data, error, isLoading } = useMeetingDecisions(projectId);
  const { importDecision } = useProjectActions();
  const [selection, setSelection] = useState<MeetingDecision | null>(null);
  const selected = data?.decisions.find((d) => d.summary_id === selection?.summary_id && d.decision_index === selection.decision_index && d.text === selection.text);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const attempt = useRef<{ body: string; id: string } | null>(null);
  const inFlight = useRef(false);
  return <ProjectDialog title="Review meeting decisions" onClose={() => { if (!busy) onClose(); }}>
    <p className="mb-3 text-sm text-slate-400">Choose a decision from a linked meeting summary and confirm its date to add it to this project.</p>
    {isLoading && <p role="status">Loading meeting decisions…</p>}
    {(error || failure) && <p role="alert">{failure || "Could not load meeting decisions."}</p>}
    {!error && data?.decisions.length === 0 && <p>No available meeting decisions.</p>}
    {!error && <form className="space-y-3" onSubmit={async (e) => {
      e.preventDefault(); if (!selected || inFlight.current) return;
      inFlight.current = true; setBusy(true); setFailure("");
      try {
        const body = JSON.stringify([selected.summary_id, selected.decision_index, date]);
        if (attempt.current?.body !== body) attempt.current = { body, id: crypto.randomUUID() };
        await importDecision(projectId, selected, date, attempt.current.id); onClose();
      }
      catch (e) { setFailure(e instanceof Error ? e.message : "Could not import decision."); }
      finally { inFlight.current = false; setBusy(false); }
    }}>
      {data?.decisions.map((d) => <label key={`${d.summary_id}-${d.decision_index}`} className="flex gap-2 rounded border border-slate-700 p-3 text-sm">
        <input type="radio" name="meeting-decision" checked={selected?.summary_id === d.summary_id && selected.decision_index === d.decision_index} onChange={() => setSelection(d)} /><span className="break-words">{d.text}<span className="block text-xs text-slate-400">{d.meeting_title}</span></span>
      </label>)}
      <label className="block text-sm">Decision date<input type="date" required className={projectInput} value={date} onChange={(e) => setDate(e.target.value)} /></label>
      <button className={projectButton} disabled={busy || !selected || !date}>{busy ? "Importing…" : "Confirm selected decision"}</button>
    </form>}
  </ProjectDialog>;
}

export function ProjectKnowledgePanel({ projectId, section, sources, openThread, targetRecordId }: {
  projectId: string; section: string; sources: ProjectSource[]; openThread: (id: string) => void;
  targetRecordId?: string | null;
}) {
  const { data, error, isLoading, mutate } = useProjectKnowledge(projectId);
  const [scopeEditor, setScopeEditor] = useState<{ scope: ProjectScope | null } | null>(null);
  const [form, setForm] = useState<{ initial?: ProjectRecord; supersedes?: ProjectRecord } | null>(null);
  const [importing, setImporting] = useState(false);
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (error || !data || !targetRecordId || scrolledTo.current === targetRecordId) return;
    const target = document.getElementById(`record-${targetRecordId}`);
    if (target) { target.scrollIntoView?.({ block: "center" }); scrolledTo.current = targetRecordId; }
  }, [data, error, targetRecordId]);
  if (isLoading) return <p role="status">Loading confirmed project state…</p>;
  if (error || !data) return <p role="alert">Could not verify project records. <button className="underline" onClick={() => void mutate()}>Retry</button></p>;
  const kind: RecordKind = section === "Approvals" ? "approval" : section === "Milestones" ? "milestone" : "decision";
  const records = data.records.filter((r) => r.kind === kind);
  return <div className="space-y-4">
    {section === "Scope" ? <>
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold text-slate-200">Confirmed scope</h2><button className={projectButton} onClick={() => setScopeEditor({ scope: data.scope })}>Edit confirmed scope</button></div>
      <p className="whitespace-pre-wrap break-words text-sm text-slate-200">{data.scope?.content || "No confirmed scope set."}</p>
      {data.scope && <p className="text-xs text-slate-400">Revision {data.scope.version} · Saved {new Date(data.scope.created_at).toLocaleString()}</p>}
      <details className="text-sm text-slate-300"><summary className="cursor-pointer">Scope history</summary><ol className="mt-3 space-y-3">{data.scope_history.map((s) => <li key={s.id} className="rounded border border-slate-700 p-3"><p>Revision {s.version} · {new Date(s.created_at).toLocaleString()}</p><p className="whitespace-pre-wrap break-words">{s.content || "(Scope cleared)"}</p></li>)}</ol></details>
    </> : <>
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold text-slate-200">{section}</h2><div className="flex flex-wrap gap-2"><button className={projectButton} onClick={() => setForm({})}>Add {kind}</button>{kind === "decision" && <button className={projectButton} onClick={() => setImporting(true)}>Review meeting decisions</button>}</div></div>
      {kind === "approval" && <p className="text-xs text-slate-400">Personal approval tracking. This does not send a request or grant formal external approval.</p>}
      {kind === "milestone" && <p className="text-xs text-slate-400">Target dates do not mark milestones done. Update their status when work is complete.</p>}
      {records.length === 0 && <p className="text-sm text-slate-400">No {section.toLowerCase()} yet.</p>}
      <ul className="space-y-3">{records.map((r) => <li id={`record-${r.id}`} key={r.id} className="space-y-2 rounded-lg border border-slate-700 p-4">
        <div className="flex flex-wrap justify-between gap-2"><h3 className="break-words font-medium text-slate-100">{r.title}</h3><span className="text-xs capitalize text-slate-400">{r.status}</span></div>
        {r.available && <>
          <p className="whitespace-pre-wrap break-words text-sm text-slate-300">{r.description}</p>
          <p className="text-xs text-slate-400">{kind === "approval" ? `Owner: ${r.owner || "Not set"} · Deadline: ${r.deadline || "Not set"}` : `${kind === "decision" ? "Decision" : "Target"} date: ${r.event_date}`}</p>
          {r.supersedes_id && <a href={`#record-${r.supersedes_id}`} className="block text-xs text-indigo-400">Replaces earlier decision</a>}
          <p className="text-xs text-slate-500">Recorded {new Date(r.created_at).toLocaleString()} · Revision {r.version}</p>
          {kind !== "decision" ? <button className={projectButton} onClick={() => setForm({ initial: r })}>Edit {kind}</button> : r.status === "current" && <button className={projectButton} onClick={() => setForm({ supersedes: r })}>Replace decision</button>}
        </>}
        <ul className="space-y-1 text-sm">{r.evidence.map((e) => <li key={e.id}>{!e.available ? <span className="text-amber-300">Evidence unavailable</span> : e.kind === "email_thread" && e.source_id ? <button className="text-indigo-400" onClick={() => openThread(e.source_id!)}>{e.title}</button> : e.href ? <Link href={e.href} className="text-indigo-400">{e.title}{e.summary_id ? " · imported meeting decision" : ""}</Link> : e.title}</li>)}</ul>
      </li>)}</ul>
    </>}
    {scopeEditor && <ScopeEditor projectId={projectId} scope={scopeEditor.scope} onClose={() => setScopeEditor(null)} />}
    {form && <ProjectRecordForm projectId={projectId} kind={kind} sources={sources} {...form} onClose={() => setForm(null)} />}
    {importing && <ImportMeetingDecision projectId={projectId} onClose={() => setImporting(false)} />}
  </div>;
}
