"use client";

import { useRef, useState } from "react";
import { useProjectActions, type ProjectRecord, type ProjectSource, type RecordKind, type RecordValues } from "@/hooks/useProjects";
import { ProjectDialog, projectButton, projectInput } from "./ProjectDialog";

export function ProjectRecordForm({ projectId, kind, sources, initial, supersedes, onClose }: {
  projectId: string; kind: RecordKind; sources: ProjectSource[]; initial?: ProjectRecord;
  supersedes?: ProjectRecord; onClose: () => void;
}) {
  const { createRecord, editRecord } = useProjectActions();
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [owner, setOwner] = useState(initial?.owner ?? "");
  const [date, setDate] = useState((kind === "approval" ? initial?.deadline : initial?.event_date) ?? "");
  const [status, setStatus] = useState(initial?.status ?? "");
  const [evidence, setEvidence] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const attempt = useRef<{ body: string; id: string } | null>(null);
  const inFlight = useRef(false);
  return <ProjectDialog title={`${initial ? "Edit" : supersedes ? "Replace" : "Add"} ${kind}`} onClose={() => { if (!busy) onClose(); }}>
    <form className="space-y-4" onSubmit={async (event) => {
      event.preventDefault(); if (inFlight.current) return;
      inFlight.current = true; setBusy(true); setError("");
      const values = { title, description, ...(kind === "approval" ? { owner, deadline: date || null } : { event_date: date }) };
      try {
        if (initial) await editRecord(projectId, initial, { ...values, status });
        else {
          const body: RecordValues = { ...values, kind, evidence: sources.filter((s) => evidence.includes(s.id) && s.source_id).map((s) => ({ kind: s.kind, source_id: s.source_id! })) };
          if (supersedes) body.supersedes_id = supersedes.id;
          const serialized = JSON.stringify(body);
          if (attempt.current?.body !== serialized) attempt.current = { body: serialized, id: crypto.randomUUID() };
          await createRecord(projectId, body, attempt.current.id);
        }
        onClose();
      } catch (e) { setError(e instanceof Error ? e.message : "Could not save record."); }
      finally { inFlight.current = false; setBusy(false); }
    }}>
      {supersedes && <p className="text-sm text-slate-400">Replacing “{supersedes.title}”. The previous decision stays in history.</p>}
      <label className="block text-sm">Title<input required maxLength={500} className={projectInput} value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="block text-sm">Details<textarea maxLength={3000} rows={3} className={projectInput} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      {kind === "approval" && <label className="block text-sm">Owner (optional)<input maxLength={200} className={projectInput} value={owner} onChange={(e) => setOwner(e.target.value)} /></label>}
      <label className="block text-sm">{kind === "approval" ? "Deadline (optional)" : kind === "milestone" ? "Target date" : "Decision date"}<input type="date" required={kind !== "approval"} className={projectInput} value={date} onChange={(e) => setDate(e.target.value)} /></label>
      {initial && <label className="block text-sm">Status<select className={projectInput} value={status} onChange={(e) => setStatus(e.target.value)}>
        {(kind === "approval" ? ["pending", "approved", "declined", "cancelled"] : ["planned", "done", "cancelled"]).map((s) => <option key={s} value={s}>{s}</option>)}
      </select></label>}
      {!initial && <fieldset className="space-y-2"><legend className="text-sm">Supporting evidence (up to 5 linked sources)</legend>
        {sources.filter((s) => s.available && s.source_id).map((s) => <label key={s.id} className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={evidence.includes(s.id)} disabled={!evidence.includes(s.id) && evidence.length >= 5} onChange={(e) => setEvidence((ids) => e.target.checked ? [...ids, s.id] : ids.filter((id) => id !== s.id))} /><span className="break-words">{s.title}</span>
        </label>)}
        {!sources.some((s) => s.available) && <p className="text-xs text-slate-400">Add sources to the project to attach evidence. You can also record your own decision without a source.</p>}
      </fieldset>}
      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
      <button disabled={busy || !title.trim()} className={projectButton}>{busy ? "Saving…" : initial ? "Save record" : "Confirm record"}</button>
    </form>
  </ProjectDialog>;
}
