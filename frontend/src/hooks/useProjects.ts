"use client";

import useSWR, { useSWRConfig } from "swr";
import { api } from "@/lib/api";

export type SourceKind = "email_thread" | "meeting" | "commitment";
export interface Project {
  id: string;
  name: string;
  description: string;
  target_date: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}
export type ProjectValues = Pick<Project, "name" | "description" | "target_date">;
export interface SourceCandidate {
  kind: SourceKind;
  source_id: string;
  title: string;
  detail: string | null;
  occurred_at: string | null;
  status: string | null;
  deadline: string | null;
  href: string | null;
}
export interface ProjectSource extends Omit<SourceCandidate, "source_id"> {
  id: string;
  source_id: string | null;
  linked_at: string;
  available: boolean;
  unavailable_reason?: string;
}
export interface ProjectDetail {
  project: Project;
  sources: ProjectSource[];
  source_count: number;
  open_commitment_count: number;
}
export interface ProjectActivity {
  id: string;
  action: string;
  source_kind: SourceKind | null;
  details: { title?: string | null; before?: string | null; after?: string | null; status?: string };
  occurred_at: string;
  event_type: "project" | "source";
}
export interface ProjectScope { id: string; content: string; version: number; created_at: string }
export type RecordKind = "decision" | "approval" | "milestone";
export interface RecordEvidence {
  id: string; kind: SourceKind; source_id: string | null; available: boolean;
  title: string; href: string | null; summary_id: string | null; decision_index: number | null;
}
export interface ProjectRecord {
  id: string; kind: RecordKind; title: string; description: string; owner: string;
  status: string; event_date: string | null; deadline: string | null;
  supersedes_id: string | null; version: number; created_at: string; updated_at: string;
  evidence: RecordEvidence[]; available: boolean;
}
export interface ProjectKnowledge {
  scope: ProjectScope | null; scope_history: ProjectScope[]; records: ProjectRecord[];
}
export interface RecordValues {
  kind: RecordKind; title: string; description: string; owner?: string;
  event_date?: string | null; deadline?: string | null; supersedes_id?: string;
  evidence?: { kind: SourceKind; source_id: string }[];
}
export interface MeetingDecision {
  meeting_id: string; meeting_title: string; summary_id: string; decision_index: number; text: string; date: string;
}
export interface UpdateClaim {
  section: string; text: string;
  time_basis: "current_context" | "source_event_this_week" | "project_action_this_week";
  citations: { evidence_id: string; quote: string }[];
}
export interface ProjectUpdateResult {
  update: { claims: UpdateClaim[]; generated_at: string; week_start: string; week_end: string; timezone: string } | null;
  stale: boolean; withheld: boolean; last_generated_at?: string;
}
export interface ProjectEvidence {
  id: string; kind: string; text: string; href: string | null; section: string | null;
  record_id: string | null; occurred_at: string | null; recorded_at: string | null;
}
export const isProjectKey = (key: unknown) => typeof key === "string" && key.startsWith("/projects");
const fetcher = <T,>(url: string) => api.get<T>(url);

export function useProjects(status: Project["status"] = "active", offset = 0) {
  return useSWR<{ projects: Project[] }>(`/projects?status=${status}&limit=50&offset=${offset}`, fetcher);
}

export function useProject(id: string) {
  return useSWR<ProjectDetail>(`/projects/${id}`, fetcher, { refreshInterval: 30_000 });
}

export function useProjectActivity(id: string, offset = 0, limit = 50, enabled = true) {
  return useSWR<{ activity: ProjectActivity[] }>(enabled ? `/projects/${id}/activity?limit=${limit}&offset=${offset}` : null, fetcher, { refreshInterval: 30_000 });
}

export function useProjectKnowledge(id: string) {
  return useSWR<ProjectKnowledge>(`/projects/${id}/knowledge`, fetcher, { refreshInterval: 30_000 });
}

export function useProjectUpdate(id: string) {
  return useSWR<ProjectUpdateResult>(`/projects/${id}/update`, fetcher, { refreshInterval: 30_000 });
}

export function useMeetingDecisions(id: string) {
  return useSWR<{ decisions: MeetingDecision[] }>(`/projects/${id}/meeting-decisions`, fetcher);
}

export function useProjectActions() {
  const { mutate } = useSWRConfig();
  async function refresh() { await mutate(isProjectKey); }
  return {
    async saveScope(id: string, content: string, expected_version: number) {
      await api.put(`/projects/${id}/scope`, { content, expected_version });
      await refresh();
    },
    async createRecord(id: string, values: RecordValues, request_id: string) {
      await api.post(`/projects/${id}/records`, { ...values, request_id });
      await refresh();
    },
    async editRecord(id: string, record: ProjectRecord, values: Partial<RecordValues> & { status?: string }) {
      await api.patch(`/projects/${id}/records/${record.id}`, { ...values, expected_version: record.version });
      await refresh();
    },
    async importDecision(id: string, decision: MeetingDecision, decision_date: string, request_id: string) {
      await api.post(`/projects/${id}/decisions/import`, { summary_id: decision.summary_id, decision_index: decision.decision_index, decision_date, request_id });
      await refresh();
    },
    async generateUpdate(id: string, request_id: string, signal: AbortSignal) {
      const result = await api.post<ProjectUpdateResult>(`/projects/${id}/update`, { request_id }, { signal });
      await mutate(`/projects/${id}/update`, result, { revalidate: false });
    },
    async create(values: ProjectValues) {
      const result = await api.post<{ project: Project }>("/projects", values);
      await refresh();
      return result.project;
    },
    async edit(id: string, values: Partial<ProjectValues> & { status?: Project["status"] }) {
      await api.patch(`/projects/${id}`, values);
      await refresh();
    },
    async link(id: string, kind: SourceKind, source_id: string) {
      await api.post(`/projects/${id}/sources`, { kind, source_id });
      await refresh();
    },
    async unlink(id: string, source: ProjectSource) {
      await api.del(`/projects/${id}/sources/${source.kind}/${source.id}`);
      await refresh();
    },
  };
}
