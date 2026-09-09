"use client";

import { useState } from "react";
import Link from "next/link";
import { useProjects, useProjectActions, type SourceKind } from "@/hooks/useProjects";
import { ProjectDialog, projectButton } from "./ProjectDialog";

function ProjectPicker({ kind, sourceId, onClose }: { kind: SourceKind; sourceId: string; onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useProjects("active", offset);
  const { link } = useProjectActions();
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState("");
  const [added, setAdded] = useState<string[]>([]);
  return <ProjectDialog title="Add to project" onClose={() => { if (!busy) onClose(); }}>
    {isLoading && <p role="status">Loading projects…</p>}
    {(error || failure) && <p role="alert" className="text-sm text-red-400">{failure || "Could not load projects."}</p>}
    {!isLoading && !error && data?.projects.length === 0 && <p className="text-sm">No active projects. <Link className="text-indigo-400" href="/projects">Create a project</Link> to organize this source.</p>}
    <ul className="space-y-2">{data?.projects.map((project) => <li key={project.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 p-3">
      <span className="break-words text-sm">{project.name}</span>
      <button className={projectButton} disabled={busy !== null || added.includes(project.id)} onClick={async () => {
        setBusy(project.id); setFailure("");
        try { await link(project.id, kind, sourceId); setAdded((ids) => [...ids, project.id]); }
        catch (e) { setFailure(e instanceof Error ? e.message : "Could not link source."); }
        finally { setBusy(null); }
      }}>{added.includes(project.id) ? "Added" : busy === project.id ? "Adding…" : "Add"}</button>
    </li>)}</ul>
    <div className="mt-3 flex justify-between text-sm">
      <button disabled={offset === 0} onClick={() => setOffset(offset - 50)} className="disabled:opacity-30">Previous</button>
      <button disabled={!data || data.projects.length < 50} onClick={() => setOffset(offset + 50)} className="disabled:opacity-30">Next</button>
    </div>
  </ProjectDialog>;
}

export function AddToProject({ kind, sourceId }: { kind: SourceKind; sourceId: string }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" onClick={() => setOpen(true)} className="rounded-md border border-slate-600 px-3 py-1.5 text-xs text-slate-300 hover:border-indigo-400">Add to project</button>
    {open && <ProjectPicker kind={kind} sourceId={sourceId} onClose={() => setOpen(false)} />}
  </>;
}
