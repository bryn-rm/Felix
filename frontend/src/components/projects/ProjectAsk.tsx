"use client";

import { useEffect, useRef, useState } from "react";
import { useProjectAnswer, useProjectActions, type UpdateClaim } from "@/hooks/useProjects";
import { CitationPreview } from "./CitationPreview";
import { projectButton } from "./ProjectDialog";

export function ProjectAskPanel({ projectId, navigate, openThread }: {
  projectId: string; navigate: (section: string, recordId?: string | null) => void;
  openThread: (id: string) => void;
}) {
  const { data, error, isLoading, mutate } = useProjectAnswer(projectId);
  const { askProject } = useProjectActions();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [citation, setCitation] = useState<{ requestId: string; value: UpdateClaim["citations"][number] } | null>(null);
  const attempt = useRef<{ question: string; id: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const answer = data?.answer;

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || controller.current) return;
    if (attempt.current?.question !== trimmed) attempt.current = { question: trimmed, id: crypto.randomUUID() };
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true); setFailure(""); setCitation(null);
    const timer = setTimeout(() => abort.abort(), 85_000);
    try {
      await askProject(projectId, trimmed, attempt.current.id, abort.signal);
      attempt.current = null;
      setQuestion("");
    } catch (e) {
      setFailure(abort.signal.aborted ? "Answering took too long. Please retry." : e instanceof Error ? e.message : "Could not answer. Please retry.");
    } finally {
      clearTimeout(timer); controller.current = null; setBusy(false);
    }
  }

  return <div className="space-y-5">
    <div>
      <h2 className="font-semibold text-slate-100">Ask this project</h2>
      <p className="mt-1 text-sm text-slate-400">Ask about linked emails, meetings, commitments, and confirmed records. Each question stands alone; the latest answer is saved here.</p>
    </div>
    <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
      <label className="block text-sm text-slate-300" htmlFor="project-question">Your question</label>
      <textarea id="project-question" value={question} onChange={(event) => setQuestion(event.target.value)}
        placeholder="Why did we move the launch date, and what remains unresolved?"
        maxLength={2000} rows={3} required disabled={busy}
        className="w-full rounded-lg border border-slate-600 bg-slate-900 p-3 text-sm text-slate-100 disabled:opacity-60" />
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-500">{question.length}/2000</span>
        <button className={projectButton} disabled={busy || !question.trim()} type="submit">{busy ? "Answering…" : "Ask project"}</button>
      </div>
    </form>
    {busy && <p role="status" className="text-sm text-slate-400">Reading project evidence…</p>}
    {failure && <p role="alert" className="text-sm text-red-400">{failure}</p>}
    {isLoading && <p role="status">Loading the latest answer…</p>}
    {error && <p role="alert" className="text-sm text-red-400">Could not verify the saved answer. <button className="underline" onClick={() => void mutate()}>Retry loading</button></p>}
    {!error && data?.withheld && <div className="space-y-2">
      {data.question && <h3 className="whitespace-pre-wrap break-words font-medium text-slate-100">{data.question}</h3>}
      <p role="status" className="text-sm text-amber-300">The saved answer is withheld because supporting evidence is no longer available. Ask again using current sources.</p>
    </div>}
    {!error && !data?.withheld && answer && <section aria-label="Project answer" className="space-y-4 rounded-lg border border-slate-700 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="whitespace-pre-wrap break-words font-medium text-slate-100">{answer.question}</h3>
        <button className={projectButton} disabled={busy} onClick={() => { setQuestion(answer.question); void ask(answer.question); }}>Ask again</button>
      </div>
      <p className="text-xs text-slate-500">Answered {new Date(answer.generated_at).toLocaleString()}</p>
      {data.stale && <p role="status" className="text-sm text-amber-300">This answer may be out of date. Ask again to use current project evidence.</p>}
      <p className="text-xs text-slate-400">Based on selected source excerpts.{answer.omitted_count > 0 ? ` ${answer.omitted_count} evidence items were outside this answer’s selection.` : ""} Missing evidence does not mean something did not happen.</p>
      {answer.claims.length === 0 && <p className="text-sm text-slate-300">There is not enough evidence to answer this question.</p>}
      <ol className="space-y-4">{answer.claims.map((claim, index) => <li key={index} className="space-y-2">
        {claim.kind === "conflict" && <p className="text-xs font-medium text-amber-300">Conflicting evidence</p>}
        <p className="whitespace-pre-wrap break-words text-sm text-slate-200">{claim.text}</p>
        <div className="flex flex-wrap gap-3">{claim.citations.map((value, i) => <button key={i} className="text-xs text-indigo-400 underline" onClick={() => setCitation({ requestId: answer.request_id, value })}>Evidence {index + 1}.{i + 1}</button>)}</div>
      </li>)}</ol>
      {answer.unanswered.length > 0 && <div className="space-y-2">
        <h4 className="text-sm font-medium text-slate-300">Missing evidence</h4>
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-400">{answer.unanswered.map((item, index) => <li key={index} className="whitespace-pre-wrap break-words">{item}</li>)}</ul>
      </div>}
    </section>}
    {!error && data && !answer && !data.withheld && <p className="text-sm text-slate-400">Try asking what was decided, why a date changed, or which commitments are still open.</p>}
    {!error && !data?.withheld && answer && citation?.requestId === answer.request_id && <CitationPreview projectId={projectId} citation={citation.value} onClose={() => setCitation(null)} navigate={navigate} openThread={openThread} />}
  </div>;
}
