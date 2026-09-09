"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useProjects, useProjectActions, type Project } from "@/hooks/useProjects";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { projectButton } from "@/components/projects/ProjectDialog";

export default function ProjectsPage() {
  const [status, setStatus] = useState<Project["status"]>("active");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error, mutate } = useProjects(status, offset);
  const { create } = useProjectActions();
  const router = useRouter();
  return <div className="mx-auto max-w-5xl space-y-5 p-4 md:p-6">
    <div className="flex items-start justify-between gap-3">
      <div><h1 className="text-xl font-semibold text-slate-100">Projects</h1><p className="mt-1 text-sm text-slate-400">Keep related conversations, meetings, and commitments together.</p></div>
      <button className={`${projectButton} shrink-0`} onClick={() => setCreating(true)}>Create project</button>
    </div>
    <div className="flex gap-2" aria-label="Project status">
      {(["active", "archived"] as const).map((value) => <button key={value} aria-pressed={status === value}
        onClick={() => { setStatus(value); setOffset(0); }} className={`rounded px-3 py-2 text-sm capitalize ${status === value ? "bg-slate-700 text-white" : "text-slate-400"}`}>{value}</button>)}
    </div>
    {isLoading && <p role="status" className="text-slate-400">Loading projects…</p>}
    {error && <p role="alert" className="text-red-400">Could not load projects. <button onClick={() => void mutate()} className="underline">Retry</button></p>}
    {!isLoading && !error && data?.projects.length === 0 && <p className="rounded-lg border border-slate-700 p-8 text-center text-slate-400">{status === "active" ? "Create your first project, then add sources to get started." : "No archived projects."}</p>}
    <div className="grid gap-3 sm:grid-cols-2">{data?.projects.map((project) => <Link key={project.id} href={`/projects/${project.id}`} className="rounded-xl border border-slate-700 bg-slate-800/40 p-4 hover:border-indigo-500">
      <h2 className="break-words font-semibold text-slate-100">{project.name}</h2>
      <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm text-slate-400">{project.description || "No description yet."}</p>
      {project.target_date && <p className="mt-3 text-xs text-slate-400">Target: {project.target_date}</p>}
    </Link>)}</div>
    <div className="flex justify-between text-sm text-slate-300">
      <button disabled={offset === 0} onClick={() => setOffset(offset - 50)} className="disabled:opacity-30">Previous</button>
      <button disabled={!data || data.projects.length < 50} onClick={() => setOffset(offset + 50)} className="disabled:opacity-30">Next</button>
    </div>
    {creating && <ProjectForm onClose={() => setCreating(false)} onSubmit={async (values) => {
      const project = await create(values); router.push(`/projects/${project.id}`);
    }} />}
  </div>;
}
