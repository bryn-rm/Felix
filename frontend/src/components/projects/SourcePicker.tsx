"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import type { Settings } from "@/lib/types";
import { useProjectActions, type ProjectSource, type SourceCandidate, type SourceKind } from "@/hooks/useProjects";
import { ProjectDialog, projectButton, projectInput } from "./ProjectDialog";

export const sourceLabels: Record<SourceKind, string> = { email_thread: "Email thread", meeting: "Meeting", commitment: "Commitment" };

export function SourcePicker({ projectId, sources, onClose }: {
  projectId: string; sources: ProjectSource[]; onClose: () => void;
}) {
  const [kind, setKind] = useState<SourceKind>("email_thread");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const { link } = useProjectActions();
  const { data: settings } = useSWR<Settings>("/settings", (url: string) => api.get<Settings>(url));
  useEffect(() => {
    const timer = setTimeout(() => { setSearch(query); setOffset(0); }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  const { data, isLoading, error: loadError } = useSWR<{ sources: SourceCandidate[] }>(
    `/projects/sources/search?kind=${kind}&q=${encodeURIComponent(search)}&limit=30&offset=${offset}`,
    (url: string) => api.get<{ sources: SourceCandidate[] }>(url),
  );
  return (
    <ProjectDialog title="Add sources" onClose={() => { if (!busy) onClose(); }}>
      <div className="space-y-3">
        <label className="block text-sm">Source type
          <select className={projectInput} value={kind} onChange={(e) => { setKind(e.target.value as SourceKind); setOffset(0); }}>
            <option value="email_thread">Email threads</option>
            {settings?.meeting_capture_mode === true && <option value="meeting">Meetings</option>}
            <option value="commitment">Commitments</option>
          </select>
        </label>
        <label className="block text-sm">Search sources
          <input className={projectInput} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search titles or people" />
        </label>
        {kind === "email_thread" && <p className="text-xs text-slate-400">Search covers email already stored by Felix, including sent mail. Adding a thread includes later replies as Felix stores them.</p>}
        {isLoading && <p role="status">Loading sources…</p>}
        {(error || loadError) && <p role="alert" className="text-sm text-red-400">{error || "Could not load sources."}</p>}
        {!isLoading && !loadError && data?.sources.length === 0 && <p className="text-sm text-slate-400">No matching sources.</p>}
        <ul className="space-y-2">
          {data?.sources.map((source) => {
            const linked = sources.some((item) => item.kind === kind && item.source_id === source.source_id);
            return <li key={source.source_id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 p-3">
              <div className="min-w-0"><p className="break-words text-sm">{source.title}</p><p className="text-xs text-slate-400">{source.detail}</p></div>
              <button className={projectButton} disabled={linked || busy !== null} onClick={async () => {
                setBusy(source.source_id); setError("");
                try { await link(projectId, kind, source.source_id); }
                catch (e) { setError(e instanceof Error ? e.message : "Could not link source."); }
                finally { setBusy(null); }
              }}>{linked ? "Added" : busy === source.source_id ? "Adding…" : "Add"}</button>
            </li>;
          })}
        </ul>
        <div className="flex justify-between text-sm">
          <button disabled={offset === 0} onClick={() => setOffset(offset - 30)} className="disabled:opacity-30">Previous</button>
          <button disabled={!data || data.sources.length < 30} onClick={() => setOffset(offset + 30)} className="disabled:opacity-30">Next</button>
        </div>
      </div>
    </ProjectDialog>
  );
}
