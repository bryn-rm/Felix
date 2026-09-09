import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig, useSWRConfig } from "swr";
import { api } from "@/lib/api";
import { ProjectKnowledgePanel } from "@/components/projects/ProjectKnowledge";
import { ProjectUpdatePanel } from "@/components/projects/ProjectUpdate";
import { ThreadPreview } from "@/components/projects/ThreadPreview";
import { ProjectDialog } from "@/components/projects/ProjectDialog";
import type { ProjectKnowledge, ProjectRecord, ProjectSource, ProjectUpdateResult } from "@/hooks/useProjects";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), put: jest.fn(), post: jest.fn(), patch: jest.fn() } }));
const navigate = jest.fn();
const openThread = jest.fn();
const source: ProjectSource = { id: "l1", kind: "email_thread", source_id: "thread-1", title: "Launch evidence", detail: "Pat", occurred_at: "2026-01-01", status: null, deadline: null, href: null, linked_at: "2026-09-09", available: true };
let knowledge: ProjectKnowledge;
let result: ProjectUpdateResult;
const record = (kind: ProjectRecord["kind"]): ProjectRecord => ({ id: "r1", kind, title: "Small launch", description: "Keep the scope small", owner: "Pat", status: kind === "decision" ? "current" : kind === "approval" ? "pending" : "planned", event_date: kind === "approval" ? null : "2026-09-09", deadline: null, supersedes_id: null, version: 1, created_at: "2026-09-09T12:00:00Z", updated_at: "2026-09-09T12:00:00Z", evidence: [], available: true });
const savedUpdate = (): ProjectUpdateResult => ({ stale: false, withheld: false, update: { claims: [{ section: "developments", text: "The old launch email was linked this week.", time_basis: "project_action_this_week", citations: [{ evidence_id: "email:sent:1", quote: "Launch evidence" }] }], generated_at: "2026-09-09T12:00:00Z", week_start: "2026-09-07T07:00:00Z", week_end: "2026-09-14T07:00:00Z", timezone: "America/Los_Angeles" } });
function mount(node: React.ReactNode) { return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, errorRetryCount: 0 }}>{node}</SWRConfig>); }
function panel(section: string) { return <ProjectKnowledgePanel projectId="p1" section={section} sources={[source]} openThread={openThread} />; }
function updatePanel() { return <ProjectUpdatePanel projectId="p1" navigate={navigate} openThread={openThread} />; }

beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(global.crypto, "randomUUID", { configurable: true, value: jest.fn(() => "attempt-1") });
  knowledge = { scope: { id: "s1", content: "User-confirmed scope", version: 2, created_at: "2026-09-09T12:00:00Z" }, scope_history: [{ id: "s0", content: "Earlier scope", version: 1, created_at: "2026-09-08T12:00:00Z" }], records: [] };
  result = { update: null, stale: false, withheld: false };
  (api.get as jest.Mock).mockImplementation(async (url: string) => {
    if (url.endsWith("/knowledge")) return { ...knowledge };
    if (url.endsWith("/update")) return { ...result };
    if (url.includes("/evidence?")) return { id: "email:sent:1", kind: "email", text: "Launch evidence from the canonical source", href: null, section: "Sources", record_id: "thread-1", occurred_at: "2026-01-01T12:00:00Z", recorded_at: null };
    if (url.endsWith("/meeting-decisions")) return { decisions: [{ meeting_id: "m1", meeting_title: "Planning", summary_id: "summary-1", decision_index: 0, text: "Ship the smaller launch", date: "2026-01-01" }] };
    throw new Error(`Unexpected ${url}`);
  });
  (api.put as jest.Mock).mockImplementation(async (_url, body) => { knowledge = { ...knowledge, scope: { ...knowledge.scope!, content: body.content, version: body.expected_version + 1 } }; });
  (api.post as jest.Mock).mockImplementation(async (url, body) => {
    if (url.endsWith("/update")) { result = savedUpdate(); return result; }
    if (url.endsWith("/records")) knowledge = { ...knowledge, records: [{ ...record(body.kind), ...body, evidence: (body.evidence ?? []).map((e: object, i: number) => ({ ...e, id: `e${i}`, available: true, title: source.title, href: null, summary_id: null, decision_index: null })) }] };
    return { id: "new-record" };
  });
  (api.patch as jest.Mock).mockImplementation(async (_url, body) => { knowledge = { ...knowledge, records: knowledge.records.map((r) => ({ ...r, ...body, version: r.version + 1 })) }; });
});

it("edits confirmed scope explicitly, retains history, and sends its version", async () => {
  mount(panel("Scope"));
  expect(await screen.findByText("User-confirmed scope")).toBeInTheDocument();
  expect(screen.getByText("Earlier scope")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit confirmed scope" }));
  fireEvent.change(screen.getByLabelText("Confirmed scope"), { target: { value: "Revised by user" } });
  fireEvent.click(screen.getByRole("button", { name: "Save confirmed scope" }));
  expect(await screen.findByText("Revised by user")).toBeInTheDocument();
  expect(api.put).toHaveBeenCalledWith("/projects/p1/scope", { content: "Revised by user", expected_version: 2 });
  expect(api.post).not.toHaveBeenCalled();
});

it("keeps the user's scope draft on a version conflict", async () => {
  (api.put as jest.Mock).mockRejectedValue(new Error("Scope changed. Reload before saving."));
  mount(panel("Scope"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit confirmed scope" }));
  fireEvent.change(screen.getByLabelText("Confirmed scope"), { target: { value: "My draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save confirmed scope" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Scope changed");
  expect(screen.getByLabelText("Confirmed scope")).toHaveValue("My draft");
});

it.each(["Decisions", "Approvals", "Milestones"])("creates a confirmed record with linked evidence in %s", async (section) => {
  const kind = section === "Decisions" ? "decision" : section === "Approvals" ? "approval" : "milestone";
  mount(panel(section));
  fireEvent.click(await screen.findByRole("button", { name: `Add ${kind}` }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Launch checkpoint" } });
  if (kind !== "approval") fireEvent.change(screen.getByLabelText(kind === "decision" ? "Decision date" : "Target date"), { target: { value: "2026-09-10" } });
  fireEvent.click(screen.getByLabelText("Launch evidence"));
  fireEvent.click(screen.getByRole("button", { name: "Confirm record" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/projects/p1/records", expect.objectContaining({ kind, title: "Launch checkpoint", evidence: [{ kind: "email_thread", source_id: "thread-1" }] })));
});

it("replaces a decision while preserving its identity in history", async () => {
  knowledge.records = [record("decision")];
  mount(panel("Decisions"));
  fireEvent.click(await screen.findByRole("button", { name: "Replace decision" }));
  expect(screen.getByText(/previous decision stays in history/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Larger launch" } });
  fireEvent.change(screen.getByLabelText("Decision date"), { target: { value: "2026-09-10" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm record" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/projects/p1/records", expect.objectContaining({ supersedes_id: "r1", title: "Larger launch" })));
  expect(api.patch).not.toHaveBeenCalled();
});

it.each(["approval", "milestone"] as const)("edits %s status with optimistic version checks", async (kind) => {
  knowledge.records = [record(kind)];
  mount(panel(kind === "approval" ? "Approvals" : "Milestones"));
  fireEvent.click(await screen.findByRole("button", { name: `Edit ${kind}` }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: kind === "approval" ? "approved" : "done" } });
  fireEvent.click(screen.getByRole("button", { name: "Save record" }));
  await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/projects/p1/records/r1", expect.objectContaining({ status: kind === "approval" ? "approved" : "done", expected_version: 1 })));
});

it("imports a meeting decision only after review and explicit confirmation", async () => {
  mount(panel("Decisions"));
  fireEvent.click(await screen.findByRole("button", { name: "Review meeting decisions" }));
  fireEvent.click(await screen.findByRole("radio"));
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Decision date"), { target: { value: "2026-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm selected decision" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/projects/p1/decisions/import", { summary_id: "summary-1", decision_index: 0, decision_date: "2026-01-01", request_id: "attempt-1" }));
});

it("shows withheld records and evidence without edit or source actions", async () => {
  knowledge.records = [{ ...record("decision"), available: false, title: "Record withheld: supporting evidence unavailable", evidence: [{ id: "e1", kind: "meeting", source_id: null, title: "Evidence unavailable", href: null, available: false, summary_id: null, decision_index: 0 }] }];
  mount(panel("Decisions"));
  expect(await screen.findByText("Evidence unavailable")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Replace decision" })).not.toBeInTheDocument();
  expect(screen.queryByText("Small launch")).not.toBeInTheDocument();
});

it("generates only on demand, labels event semantics, and never changes confirmed state", async () => {
  mount(updatePanel());
  await screen.findByText(/Nothing is generated automatically/);
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Generate update" }));
  expect(await screen.findByText("The old launch email was linked this week.")).toBeInTheDocument();
  expect(screen.getByText(/Project action this week/)).toBeInTheDocument();
  expect(screen.getByText(/America\/Los_Angeles/)).toBeInTheDocument();
  expect(api.post).toHaveBeenCalledWith("/projects/p1/update", { request_id: "attempt-1" }, { signal: expect.any(AbortSignal) });
  expect(api.put).not.toHaveBeenCalled();
  expect(api.patch).not.toHaveBeenCalled();
});

it("revalidates citations and opens a sent-only canonical thread", async () => {
  result = savedUpdate();
  mount(updatePanel());
  fireEvent.click(await screen.findByRole("button", { name: "Evidence 1.1" }));
  const dialog = screen.getByRole("dialog");
  expect(await within(dialog).findByText("Launch evidence")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Open thread" }));
  expect(openThread).toHaveBeenCalledWith("thread-1");
  expect(api.get).toHaveBeenCalledWith("/projects/p1/evidence?key=email%3Asent%3A1");
});

it("keeps the last update visible and retries the same request after generation failure", async () => {
  result = { ...savedUpdate(), stale: true };
  (api.post as jest.Mock).mockRejectedValue(new Error("Could not generate a valid project update"));
  mount(updatePanel());
  expect(await screen.findByText(/This update is stale/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Regenerate update" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not generate");
  expect(screen.getByText("The old launch email was linked this week.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Regenerate update" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  expect((api.post as jest.Mock).mock.calls[0][1]).toEqual((api.post as jest.Mock).mock.calls[1][1]);
});

it("withholds generated prose when its evidence is unavailable", async () => {
  result = { update: null, stale: true, withheld: true };
  mount(updatePanel());
  expect(await screen.findByText(/saved update is withheld/)).toBeInTheDocument();
  expect(screen.queryByText("The old launch email was linked this week.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Evidence 1.1" })).not.toBeInTheDocument();
});

it("does not reveal old citation text when evidence revalidation fails", async () => {
  result = savedUpdate();
  const base = (api.get as jest.Mock).getMockImplementation()!;
  (api.get as jest.Mock).mockImplementation((url: string) => url.includes("/evidence?") ? Promise.reject(new Error("Unavailable")) : base(url));
  mount(updatePanel());
  fireEvent.click(await screen.findByRole("button", { name: "Evidence 1.1" }));
  const dialog = screen.getByRole("dialog");
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Evidence unavailable");
  expect(within(dialog).queryByText("Launch evidence")).not.toBeInTheDocument();
});

it("removes cached thread content after access revalidation fails", async () => {
  let refresh: () => Promise<unknown>;
  function Preview() {
    const { mutate } = useSWRConfig();
    refresh = () => mutate("/projects/p1/threads/thread-1");
    return <ThreadPreview projectId="p1" threadId="thread-1" onClose={jest.fn()} />;
  }
  (api.get as jest.Mock).mockResolvedValue({ messages: [{ id: "mail1", subject: "Private subject", body: "Private body", direction: "sent", participant: "Pat" }] });
  mount(<Preview />);
  expect(await screen.findByText("Private body")).toBeInTheDocument();
  (api.get as jest.Mock).mockRejectedValue(new Error("Source unavailable"));
  await act(async () => { await refresh(); });
  expect(await screen.findByRole("alert")).toHaveTextContent("Thread unavailable");
  expect(screen.queryByText("Private body")).not.toBeInTheDocument();
  expect(screen.queryByText("Private subject")).not.toBeInTheDocument();
});

it("targets a cited record after confirmed state finishes loading", async () => {
  let finish: (value: ProjectKnowledge) => void;
  (api.get as jest.Mock).mockReturnValue(new Promise<ProjectKnowledge>((resolve) => { finish = resolve; }));
  const scroll = jest.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scroll });
  mount(<ProjectKnowledgePanel projectId="p1" section="Decisions" sources={[]} openThread={openThread} targetRecordId="r1" />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading confirmed");
  await act(async () => { finish({ ...knowledge, records: [record("decision")] }); });
  await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: "center" }));
  expect(document.getElementById("record-r1")).toBeInTheDocument();
});

it("recovers when request ID generation throws", async () => {
  (crypto.randomUUID as jest.Mock).mockImplementationOnce(() => { throw new Error("UUID unavailable"); });
  mount(updatePanel());
  fireEvent.click(await screen.findByRole("button", { name: "Generate update" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("UUID unavailable");
  expect(screen.getByRole("button", { name: "Generate update" })).toBeEnabled();
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Generate update" }));
  expect(await screen.findByText("The old launch email was linked this week.")).toBeInTheDocument();
});

it("retains a meeting selection across revalidation and clears changed evidence", async () => {
  let refresh: () => Promise<unknown>;
  function Panel() {
    const { mutate } = useSWRConfig();
    refresh = () => mutate("/projects/p1/meeting-decisions");
    return panel("Decisions");
  }
  mount(<Panel />);
  fireEvent.click(await screen.findByRole("button", { name: "Review meeting decisions" }));
  fireEvent.click(await screen.findByRole("radio"));
  fireEvent.change(screen.getByLabelText("Decision date"), { target: { value: "2026-01-01" } });
  // A changed meeting title forces SWR to publish new objects for the same decision.
  const original = (api.get as jest.Mock).getMockImplementation()!;
  (api.get as jest.Mock).mockImplementation(async (url) => {
    const data = await original(url);
    return url.endsWith("/meeting-decisions") ? { decisions: data.decisions.map((d: object) => ({ ...d, meeting_title: "New meeting title" })) } : data;
  });
  await act(async () => { await refresh(); });
  expect(screen.getByRole("radio")).toBeChecked();
  expect(screen.getByRole("button", { name: "Confirm selected decision" })).toBeEnabled();
  (api.get as jest.Mock).mockImplementation(async (url) => {
    const data = await original(url);
    return url.endsWith("/meeting-decisions") ? { decisions: data.decisions.map((d: object) => ({ ...d, text: "Changed decision" })) } : data;
  });
  await act(async () => { await refresh(); });
  expect(screen.getByRole("radio")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Confirm selected decision" })).toBeDisabled();
});

it("keeps a draft open when a pointer drag starts inside and ends on the backdrop", () => {
  const close = jest.fn();
  mount(<ProjectDialog title="Draft" onClose={close}><textarea aria-label="Draft text" defaultValue="Unsaved" /></ProjectDialog>);
  const backdrop = screen.getByRole("dialog").parentElement!;
  fireEvent.pointerDown(screen.getByLabelText("Draft text"));
  fireEvent.pointerUp(backdrop);
  fireEvent.click(backdrop);
  expect(close).not.toHaveBeenCalled();
  fireEvent.pointerDown(backdrop);
  fireEvent.pointerUp(backdrop);
  fireEvent.click(backdrop);
  expect(close).toHaveBeenCalledTimes(1);
});

it("retries a confirmed record with the same request ID after transport failure", async () => {
  (api.post as jest.Mock).mockRejectedValue(new Error("Connection lost"));
  mount(panel("Decisions"));
  fireEvent.click(await screen.findByRole("button", { name: "Add decision" }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Retry decision" } });
  fireEvent.change(screen.getByLabelText("Decision date"), { target: { value: "2026-09-09" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm record" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Confirm record" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  expect((api.post as jest.Mock).mock.calls[1][1]).toEqual((api.post as jest.Mock).mock.calls[0][1]);
  expect((api.post as jest.Mock).mock.calls[0][1].request_id).toBe("attempt-1");
});
