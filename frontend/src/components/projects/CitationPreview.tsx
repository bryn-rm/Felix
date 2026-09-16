"use client";

import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import type { ProjectEvidence, UpdateClaim } from "@/hooks/useProjects";
import { ProjectDialog, projectButton } from "./ProjectDialog";

export function CitationPreview({ projectId, citation, onClose, navigate, openThread }: {
  projectId: string; citation: UpdateClaim["citations"][number]; onClose: () => void;
  navigate: (section: string, recordId?: string | null) => void; openThread: (id: string) => void;
}) {
  const { data, error, isLoading } = useSWR<ProjectEvidence>(
    `/projects/${projectId}/evidence?key=${encodeURIComponent(citation.evidence_id)}`,
    (url: string) => api.get<ProjectEvidence>(url), { refreshInterval: 30_000 },
  );
  return <ProjectDialog title="Supporting evidence" onClose={onClose}>
    {isLoading && <p role="status">Checking evidence…</p>}
    {error && <p role="alert">Evidence unavailable. It may have changed or been removed from this project.</p>}
    {!error && data && <div className="space-y-3 text-sm">
      {data.text.includes(citation.quote) ? <blockquote className="whitespace-pre-wrap break-words border-l-2 border-indigo-400 pl-3">{citation.quote}</blockquote> : <p className="text-amber-300">The source has changed since generation. Showing current evidence below.</p>}
      <p className="whitespace-pre-wrap break-words text-slate-300">{data.text}</p>
      {data.occurred_at && <p className="text-xs text-slate-400">Source event: {new Date(data.occurred_at).toLocaleString()}</p>}
      {data.recorded_at && <p className="text-xs text-slate-400">Project action: {new Date(data.recorded_at).toLocaleString()}</p>}
      {data.href ? <Link className="text-indigo-400" href={data.href}>Open source</Link>
        : data.kind === "email" && data.record_id ? <button className={projectButton} onClick={() => { onClose(); openThread(data.record_id!); }}>Open thread</button>
        : data.section && <button className={projectButton} onClick={() => { onClose(); navigate(data.section!, data.record_id); }}>Open {data.section.toLowerCase()}</button>}
    </div>}
  </ProjectDialog>;
}

