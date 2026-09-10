"use client";

import { useState } from "react";
import Link from "next/link";
import { useProject, useProjectActions, useProjectActivity, type ProjectSource } from "@/hooks/useProjects";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { SourcePicker, sourceLabels } from "@/components/projects/SourcePicker";
import { ProjectActivityList } from "@/components/projects/ProjectActivity";
import { ThreadPreview } from "@/components/projects/ThreadPreview";
import { projectButton } from "@/components/projects/ProjectDialog";
import { ProjectKnowledgePanel } from "@/components/projects/ProjectKnowledge";
import { ProjectSuggestionsPanel } from "@/components/projects/ProjectSuggestions";
import { ProjectUpdatePanel } from "@/components/projects/ProjectUpdate";

export default function ProjectPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const { data, error, isLoading, mutate } = useProject(id);
  const [offset, setOffset] = useState(0);
  const { edit, unlink } = useProjectActions();
  const [tab, setTab] = useState("Overview");
  const activity = useProjectActivity(id, tab === "Activity" ? offset : 0, tab === "Activity" ? 50 : 5, tab === "Overview" || tab === "Activity");
  const [targetRecordId, setTargetRecordId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [thread, setThread] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState("");
  function navigate(section: string, recordId?: string | null) {
    setTab(section);
    setTargetRecordId(recordId ?? null);
  }

  if (isLoading) return <p role="status" className="p-6 text-slate-400">Loading project…</p>;
  if (error || !data) return <div className="space-y-3 p-6 text-slate-300"><p role="alert">Project unavailable. It may not exist or you may not have access.</p><button onClick={() => void mutate()} className={projectButton}>Retry</button><Link href="/projects" className="ml-3 text-indigo-400">Back to projects</Link></div>;
  const { project, sources } = data;
  async function remove(source: ProjectSource) {
    setBusy(source.id); setFailure("");
    try { await unlink(id, source); }
    catch (e) { setFailure(e instanceof Error ? e.message : "Could not remove source."); }
    finally { setBusy(null); }
  }
  return <div className="mx-auto max-w-5xl space-y-5 p-4 md:p-6">
    <Link href="/projects" className="text-sm text-indigo-400">← Projects</Link>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="break-words text-xl font-semibold text-slate-100">{project.name}</h1><p className="mt-1 text-xs capitalize text-slate-400">{project.status}</p></div>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => setEditing(true)} className={projectButton}>Edit project</button>
        <button disabled={busy !== null} className="rounded border border-slate-600 px-3 py-2 text-sm text-slate-300 disabled:opacity-50" onClick={async () => {
          setBusy("status"); setFailure("");
          try { await edit(id, { status: project.status === "active" ? "archived" : "active" }); }
          catch (e) { setFailure(e instanceof Error ? e.message : "Could not update project."); }
          finally { setBusy(null); }
        }}>{project.status === "active" ? "Archive project" : "Reopen project"}</button>
      </div>
    </div>
    {failure && <p role="alert" className="text-sm text-red-400">{failure}</p>}
    <div role="tablist" aria-label="Project sections" className="flex flex-wrap gap-2 border-b border-slate-700">
      {["Overview", "Scope", "Decisions", "Approvals", "Milestones", "Sources", "Activity"].map((value) => <button key={value} role="tab" aria-selected={tab === value} aria-controls="project-panel" id={`tab-${value}`} onClick={() => setTab(value)} className={`px-3 py-2 text-sm ${tab === value ? "border-b-2 border-indigo-400 text-white" : "text-slate-400"}`}>{value}</button>)}
    </div>
    <section role="tabpanel" id="project-panel" aria-labelledby={`tab-${tab}`} className="space-y-4">
      {tab === "Overview" && <>
        <ProjectUpdatePanel projectId={id} navigate={navigate} openThread={setThread} />
        <p className="whitespace-pre-wrap break-words text-sm text-slate-200">{project.description || "Add a description to explain what this project is about."}</p>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <div className="rounded-lg border border-slate-700 p-4"><dt className="text-slate-400">Target date</dt><dd className="mt-1 text-slate-100">{project.target_date || "Not set"}</dd></div>
          <div className="rounded-lg border border-slate-700 p-4"><dt className="text-slate-400">Linked sources</dt><dd className="mt-1 text-slate-100">{data.source_count}</dd></div>
          <div className="rounded-lg border border-slate-700 p-4"><dt className="text-slate-400">Open commitments</dt><dd className="mt-1 text-slate-100">{data.open_commitment_count}</dd></div>
        </dl>
        <button className={projectButton} onClick={() => setAdding(true)}>Add sources</button>
        <h2 className="font-semibold text-slate-200">Recent activity</h2>
        {activity.error ? <p role="alert" className="text-red-400">Could not load activity.</p> : activity.isLoading ? <p role="status">Loading activity…</p> : <ProjectActivityList activity={activity.data?.activity ?? []} />}
      </>}
      {["Scope", "Decisions", "Approvals", "Milestones"].includes(tab) && <ProjectKnowledgePanel key={tab} projectId={id} section={tab} sources={sources} openThread={setThread} targetRecordId={targetRecordId} />}
      {tab === "Sources" && <>
        <ProjectSuggestionsPanel projectId={id} />
        <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-slate-400">Removing a source only removes its link to this project.</p><button className={projectButton} onClick={() => setAdding(true)}>Add sources</button></div>
        {sources.length === 0 && <p className="text-sm text-slate-400">No sources linked yet.</p>}
        <ul className="space-y-3">{sources.map((source) => <li key={source.id} className="rounded-lg border border-slate-700 bg-slate-800/40 p-4">
          <div className="flex items-start justify-between gap-3"><div className="min-w-0">
            <p className="text-xs text-slate-400">{sourceLabels[source.kind]}</p>
            <p className="mt-1 break-words text-sm font-medium text-slate-100">{source.title}</p>
            {!source.available ? <p className="mt-1 text-sm text-amber-300">{source.unavailable_reason}</p> : <>
              <p className="text-xs text-slate-400">{source.detail}</p>
              {source.status && <p className="mt-1 text-xs text-slate-300">Status: {source.status}{source.deadline && ` · Due ${new Date(source.deadline).toLocaleString()}`}</p>}
              {source.kind === "email_thread" && source.source_id ? <button className="mt-2 text-sm text-indigo-400" onClick={() => setThread(source.source_id)}>View thread</button>
                : source.href && <Link href={source.href} className="mt-2 inline-block text-sm text-indigo-400">Open {sourceLabels[source.kind].toLowerCase()}</Link>}
            </>}
            <p className="mt-2 text-xs text-slate-500">Linked {new Date(source.linked_at).toLocaleString()}</p>
            {source.available && source.occurred_at && <p className="text-xs text-slate-500">{source.kind === "email_thread" ? "Latest stored message" : "Source date"}: {new Date(source.occurred_at).toLocaleString()}</p>}
          </div><button className="shrink-0 rounded px-2 py-1 text-xs text-slate-400 hover:text-red-300 disabled:opacity-50" disabled={busy !== null} onClick={() => void remove(source)} aria-label={`Remove ${source.title}`}>{busy === source.id ? "Removing…" : "Remove"}</button></div>
        </li>)}</ul>
      </>}
      {tab === "Activity" && <>
        <p className="text-xs text-slate-400">Newest first. Original events come from currently linked sources and may predate this project. Project actions record when you made a change.</p>
        {activity.error ? <p role="alert" className="text-red-400">Could not load activity.</p> : activity.isLoading ? <p role="status">Loading activity…</p> : <ProjectActivityList activity={activity.data?.activity ?? []} />}
        <div className="flex justify-between text-sm text-slate-300"><button disabled={offset === 0} className="disabled:opacity-30" onClick={() => setOffset(offset - 50)}>Newer</button><button disabled={!activity.data || activity.data.activity.length < 50} className="disabled:opacity-30" onClick={() => setOffset(offset + 50)}>Older</button></div>
      </>}
    </section>
    {editing && <ProjectForm initial={project} onClose={() => setEditing(false)} onSubmit={(values) => edit(id, values)} />}
    {adding && <SourcePicker projectId={id} sources={sources} onClose={() => setAdding(false)} />}
    {thread && <ThreadPreview projectId={id} threadId={thread} onClose={() => setThread(null)} />}
  </div>;
}
