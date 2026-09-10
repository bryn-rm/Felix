"use client";

import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { isHtml, SafeHtmlBody } from "@/components/email/EmailDetail";
import { ProjectDialog } from "./ProjectDialog";

interface Message {
  id: string; subject: string; participant: string; body: string | null;
  occurred_at: string | null; direction: "inbound" | "sent";
}

export function ThreadPreview({ projectId, threadId, suggestionId, onClose }: {
  projectId: string; threadId: string; suggestionId?: string; onClose: () => void;
}) {
  const { data, error, isLoading } = useSWR<{ messages: Message[] }>(
    suggestionId ? `/projects/${projectId}/suggestions/${suggestionId}/thread` : `/projects/${projectId}/threads/${encodeURIComponent(threadId)}`, (url: string) => api.get<{ messages: Message[] }>(url),
    { refreshInterval: 30_000 },
  );
  return <ProjectDialog title="Email thread" onClose={onClose}>
    <p className="mb-3 text-xs text-slate-400">The latest 100 messages stored by Felix, newest first.</p>
    {isLoading && <p role="status">Loading thread…</p>}
    {error && <p role="alert">Thread unavailable. It may have been removed from this project.</p>}
    {!error && data?.messages.length === 0 && <p>This thread is no longer stored in Felix.</p>}
    <div className="space-y-4">{!error && data?.messages.map((message) => <article key={`${message.direction}-${message.id}`} className="rounded-lg border border-slate-700 p-3">
      <h3 className="text-sm font-semibold">{message.subject || "(no subject)"}</h3>
      <p className="my-2 text-xs text-slate-400">{message.direction === "sent" ? "To" : "From"}: {message.participant} · {message.occurred_at && new Date(message.occurred_at).toLocaleString()}</p>
      {message.body && (isHtml(message.body)
        ? <SafeHtmlBody html={message.body} />
        : <p className="whitespace-pre-wrap break-words text-sm">{message.body}</p>)}
      {message.direction === "inbound" && <Link href={`/inbox/${encodeURIComponent(message.id)}`} className="mt-3 inline-block text-xs text-indigo-400">Open email</Link>}
    </article>)}</div>
  </ProjectDialog>;
}
