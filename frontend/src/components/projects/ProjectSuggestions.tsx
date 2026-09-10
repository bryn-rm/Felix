"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useProjectActions, useProjectSuggestions, type ProjectSuggestion } from "@/hooks/useProjects";
import { projectButton } from "./ProjectDialog";
import { sourceLabels } from "./SourcePicker";
import { ThreadPreview } from "./ThreadPreview";

export function ProjectSuggestionsPanel({ projectId }: { projectId: string }) {
  const { data, error, isLoading, mutate } = useProjectSuggestions(projectId);
  const { discoverSources, resolveSuggestion, resetSuggestionDismissals } = useProjectActions();
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState("");
  const [thread, setThread] = useState<ProjectSuggestion | null>(null);
  const requestId = useRef<string | null>(null);
  const inFlight = useRef(false);
  async function discover() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy("discovery"); setFailure("");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 85_000);
    try {
      requestId.current ??= crypto.randomUUID();
      await discoverSources(projectId, requestId.current, controller.signal);
      requestId.current = null;
    } catch (e) { setFailure(controller.signal.aborted ? "Discovery timed out. Please retry." : e instanceof Error ? e.message : "Could not find related items. Please retry."); }
    finally { clearTimeout(timer); inFlight.current = false; setBusy(null); }
  }
  async function resolve(item: ProjectSuggestion, action: "accept" | "dismiss") {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(item.id); setFailure("");
    try { await resolveSuggestion(projectId, item.id, action); }
    catch (e) {
      setFailure(e instanceof Error ? e.message : "Could not update suggestion. Please retry.");
      await mutate();
    } finally { inFlight.current = false; setBusy(null); }
  }
  async function resetDismissals() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy("reset"); setFailure("");
    try { await resetSuggestionDismissals(projectId); }
    catch (e) { setFailure(e instanceof Error ? e.message : "Could not reset dismissed items. Please retry."); }
    finally { inFlight.current = false; setBusy(null); }
  }
  return <div className="space-y-3 rounded-lg border border-indigo-800/60 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="font-semibold text-slate-100">Suggested sources</h2>
      <button className={projectButton} disabled={busy !== null} onClick={() => void discover()}>{busy === "discovery" ? "Finding related items…" : "Find related items"}</button>
    </div>
    <p className="text-xs text-slate-400">Search when you choose. Suggestions become project context only when you add them. Dismissed items stay hidden until you reset them.</p>
    {!error && !!data?.dismissed_count && <button className="text-xs text-indigo-400 disabled:opacity-50" disabled={busy !== null} onClick={() => void resetDismissals()}>Reset dismissed items ({data.dismissed_count})</button>}
    {!error && data?.stale && busy !== "discovery" && <p role="status" className="rounded border border-amber-700/60 bg-amber-950/30 p-3 text-sm text-amber-200">Saved suggestions are out of date because project context, source content, access, or discovery settings changed. Find related items again to refresh them.</p>}
    {failure && <p role="alert" className="text-sm text-red-400">{failure}</p>}
    {(isLoading || busy === "discovery") && <p role="status" className="text-sm text-slate-300">{busy === "discovery" ? "Checking existing Felix sources…" : "Loading saved suggestions…"}</p>}
    {error && <p role="alert" className="text-sm text-red-400">Could not verify suggestions. <button className="underline" onClick={() => void mutate()}>Retry</button></p>}
    {!error && data && !data.stale && data.suggestions.length === 0 && busy !== "discovery" && <p role="status" className="text-sm text-slate-400">{data.last_discovered_at ? "No related items found. Run discovery again as your project evolves." : "Find related emails, meetings, and commitments already in Felix."}</p>}
    {!error && <ul className="space-y-3">{data?.suggestions.map(item => <li key={item.id} className="space-y-2 rounded-lg border border-slate-700 p-3">
      <p className="text-xs text-slate-400">{sourceLabels[item.kind]} · Suggested{item.occurred_at && ` · ${new Date(item.occurred_at).toLocaleDateString()}`}</p>
      <h3 className="break-words text-sm font-medium text-slate-100">{item.title}</h3>
      {item.detail && <p className="break-words text-xs text-slate-400">{item.detail}</p>}
      <p className="break-words text-sm text-indigo-200">{item.explanation}</p>
      {item.preview && <p className="break-words text-xs text-slate-400">{item.preview}</p>}
      <div className="flex flex-wrap items-center gap-3">
        {item.kind === "email_thread" ? <button className="text-sm text-indigo-400" onClick={() => setThread(item)}>Open thread</button> : item.href && <Link href={item.href} className="text-sm text-indigo-400">Open source</Link>}
        <button className={projectButton} disabled={busy !== null} onClick={() => void resolve(item, "accept")}>{busy === item.id ? "Saving…" : "Add to project"}</button>
        <button className="px-2 py-2 text-sm text-slate-400 disabled:opacity-50" disabled={busy !== null} onClick={() => void resolve(item, "dismiss")}>Dismiss</button>
      </div>
    </li>)}</ul>}
    {thread && <ThreadPreview projectId={projectId} threadId={thread.source_id} suggestionId={thread.id} onClose={() => setThread(null)} />}
  </div>;
}
