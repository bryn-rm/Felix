import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { api } from "@/lib/api";
import ProjectsPage from "@/app/(app)/projects/page";
import ProjectPage from "@/app/(app)/projects/[id]/page";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { EmailDetail } from "@/components/email/EmailDetail";
import MeetingPage from "@/app/(app)/meetings/[id]/page";
import CommitmentsPage from "@/app/(app)/commitments/page";
import { AppShell } from "@/components/layout/AppShell";
import type { Email, Commitment } from "@/lib/types";
import type { Project, ProjectSource } from "@/hooks/useProjects";
import { useCommitments } from "@/hooks/useCommitments";

const push = jest.fn();
const searchParams = new URLSearchParams("direction=all&status=done");
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }), usePathname: () => "/projects/p1", useSearchParams: () => searchParams,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), del: jest.fn() } }));
jest.mock("@/hooks/useUnreadCounts", () => ({ useUnreadCounts: () => ({ actionRequired: 0, overdueFollowups: 0 }) }));
jest.mock("@/hooks/useMeetings", () => ({
  useMeeting: () => ({ meeting: { id: "m1", title: "Planning", status: "done" }, segments: [], summary: null }),
  useMeetings: () => ({ deleteMeeting: jest.fn() }),
}));
jest.mock("@/components/auth/AuthSync", () => ({ clearAllSWR: jest.fn() }));
jest.mock("@/components/layout/GoogleDisconnectedBanner", () => ({ GoogleDisconnectedBanner: () => null }));
jest.mock("@/components/felix/VoiceContext", () => ({ VoiceProvider: ({ children }: { children: React.ReactNode }) => children, useVoiceContext: () => ({ modalOpen: false }) }));
jest.mock("@/components/felix/FloatingVoiceFab", () => ({ FloatingVoiceFab: () => null }));
jest.mock("@/components/felix/VoiceModal", () => ({ VoiceModal: () => null }));

let project: Project;
let sources: ProjectSource[];
let meetingsEnabled: boolean;
let catalog: ProjectSource[];
const source = (kind: ProjectSource["kind"], id = "s1"): ProjectSource => ({
  id: `link-${id}`, kind, source_id: id, title: "Launch plan", detail: "Pat", occurred_at: "2026-01-01T10:00:00Z",
  status: kind === "commitment" ? "open" : null, deadline: null, href: "/commitments", linked_at: "2026-09-09T12:00:00Z", available: true,
});

function mount(element: React.ReactNode) {
  return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, errorRetryCount: 0 }}>{element}</SWRConfig>);
}

beforeEach(() => {
  jest.clearAllMocks();
  meetingsEnabled = true;
  sources = [];
  catalog = [source("email_thread", "thread-1")];
  project = { id: "p1", name: "Website launch", description: "Launch the site", target_date: "2026-12-31", status: "active", created_at: "2026-09-09T12:00:00Z", updated_at: "2026-09-09T12:00:00Z" };
  (api.get as jest.Mock).mockImplementation(async (url: string) => {
    if (url === "/settings") return { meeting_capture_mode: meetingsEnabled };
    if (url === "/projects/p1/update") return { update: null, stale: false, withheld: false };
    if (url === "/projects/p1/ask") return { answer: null, stale: false, withheld: false };
    if (url === "/projects/p1/suggestions") return { suggestions: [], last_discovered_at: null };
    if (url.startsWith("/projects?")) return { projects: [project] };
    if (url.includes("/sources/search")) return { sources: catalog };
    if (url.includes("/activity")) return { activity: [
      { id: "a1", action: "linked", source_kind: "email_thread", details: {}, occurred_at: "2026-09-09T12:00:00Z", event_type: "project" },
      { id: "a2", action: "email_received", source_kind: "email_thread", details: { title: "Original message" }, occurred_at: "2026-01-01T10:00:00Z", event_type: "source" },
    ] };
    if (url.includes("/threads/")) return { messages: [{ id: "sent1", subject: "Sent reply", body: "Here is the plan", direction: "sent", participant: "Pat" }] };
    if (url === "/projects/p1") return { project: { ...project }, sources: [...sources], source_count: sources.length, open_commitment_count: sources.filter((s) => s.kind === "commitment" && s.status === "open").length };
    if (url.startsWith("/commitments?")) return { commitments: [{ id: "c1", text: "Send plan", direction: "owed_to_user", status: "done", deadline: null } as Commitment] };
    throw new Error(`Unexpected URL ${url}`);
  });
  (api.post as jest.Mock).mockImplementation(async (url, body) => {
    if (url === "/projects") { project = { ...project, ...body }; return { project }; }
    if (url === "/commitments/c1/resolve") {
      sources = sources.map((item) => ({ ...item, status: body.status }));
      return {};
    }
    sources = [source(body.kind, body.source_id)];
    return { id: "link-s1" };
  });
  (api.patch as jest.Mock).mockImplementation(async (_, body) => { project = { ...project, ...body }; return { project }; });
  (api.del as jest.Mock).mockImplementation(async () => { sources = []; });
});

it("opens project questions from the Ask tab without generating an answer", async () => {
  mount(<ProjectPage params={{ id: "p1" }} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Ask" }));
  expect(await screen.findByRole("heading", { name: "Ask this project" })).toBeInTheDocument();
  expect(screen.getByLabelText("Your question")).toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
});

it("creates a project and opens its workspace", async () => {
  mount(<ProjectsPage />);
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Project name"), { target: { value: "Product launch" } });
  fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "A new product" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create project" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/projects", { name: "Product launch", description: "A new product", target_date: null }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/p1"));
});

it("keeps entered values and shows errors when saving fails", async () => {
  const submit = jest.fn().mockRejectedValue(new Error("Save failed"));
  mount(<ProjectForm initial={project} onSubmit={submit} onClose={jest.fn()} />);
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Renamed" } });
  fireEvent.change(screen.getByLabelText("Target date (optional)"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Save failed");
  expect(submit).toHaveBeenCalledWith({ name: "Renamed", description: "Launch the site", target_date: null });
  expect(screen.getByLabelText("Project name")).toHaveValue("Renamed");
});

it("edits and archives/reopens a project", async () => {
  mount(<ProjectPage params={{ id: "p1" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit project" }));
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Launch v2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("heading", { name: "Launch v2" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Archive project" }));
  fireEvent.click(await screen.findByRole("button", { name: "Reopen project" }));
  expect(await screen.findByRole("button", { name: "Archive project" })).toBeInTheDocument();
  expect(api.patch).toHaveBeenCalledWith("/projects/p1", { status: "archived" });
  expect(api.patch).toHaveBeenCalledWith("/projects/p1", { status: "active" });
});

it("links a whole email thread through the picker and prevents duplicate clicks", async () => {
  mount(<ProjectPage params={{ id: "p1" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add sources" }));
  expect(screen.getByText(/email already stored by Felix/)).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Add" }));
  expect(await screen.findByRole("button", { name: "Added" })).toBeDisabled();
  expect(api.post).toHaveBeenCalledWith("/projects/p1/sources", { kind: "email_thread", source_id: "thread-1" });
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
  fireEvent.click(screen.getByRole("button", { name: "View thread" }));
  expect(await screen.findByText("Here is the plan")).toBeInTheDocument();
});

it("searches stored sources and respects the meetings feature gate", async () => {
  meetingsEnabled = false;
  mount(<ProjectPage params={{ id: "p1" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add sources" }));
  await screen.findByRole("button", { name: "Add" });
  expect(screen.queryByRole("option", { name: "Meetings" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search sources"), { target: { value: "launch" } });
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/projects/sources/search?kind=email_thread&q=launch&limit=30&offset=0"));
});

it("shows unavailable sources explicitly and removes only their association", async () => {
  sources = [{ ...source("meeting"), source_id: null, available: false, title: "Source unavailable", href: null, unavailable_reason: "Deleted or meeting access is disabled." }];
  mount(<ProjectPage params={{ id: "p1" }} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Sources" }));
  expect(screen.getByText("Deleted or meeting access is disabled.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Open meeting" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove Source unavailable" }));
  expect(await screen.findByText("No sources linked yet.")).toBeInTheDocument();
  expect(api.del).toHaveBeenCalledTimes(1);
  expect(api.del).toHaveBeenCalledWith("/projects/p1/sources/meeting/link-s1");
});

it("distinguishes original source events from project actions", async () => {
  mount(<ProjectPage params={{ id: "p1" }} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Activity" }));
  expect(await screen.findByText(/Original source event ·/)).toBeInTheDocument();
  expect(screen.getByText(/Project action ·/)).toBeInTheDocument();
  expect(screen.getByText(/Email received: Original message/)).toBeInTheDocument();
});

it.each(["email", "meeting", "commitment"])("offers Add to project on the %s surface", async (kind) => {
  const element = kind === "email" ? <EmailDetail email={{ id: "message-id", thread_id: "thread-id", body: "hello", received_at: "2026-01-01T00:00:00Z" } as Email} />
    : kind === "meeting" ? <MeetingPage params={{ id: "m1" }} /> : <CommitmentsPage />;
  mount(element);
  fireEvent.click(await screen.findByRole("button", { name: "Add to project" }));
  fireEvent.click(await screen.findByRole("button", { name: "Add" }));
  await screen.findByRole("button", { name: "Added" });
  expect(api.post).toHaveBeenCalledWith("/projects/p1/sources", {
    kind: kind === "email" ? "email_thread" : kind,
    source_id: kind === "email" ? "thread-id" : kind === "meeting" ? "m1" : "c1",
  });
});

it("adds project navigation on desktop and mobile with other gates off", async () => {
  meetingsEnabled = false;
  mount(<AppShell userEmail="pat@example.com" displayName="Pat"><p>Project workspace</p></AppShell>);
  const desktop = screen.getByRole("complementary", { name: "Primary navigation" });
  expect(within(desktop).getByTitle("Projects")).toHaveAttribute("href", "/projects");
  const mobile = screen.getByRole("navigation", { name: "Mobile navigation" });
  expect(within(mobile).getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/projects");
  await waitFor(() => expect(within(mobile).queryByRole("link", { name: "Meetings" })).not.toBeInTheDocument());
  expect(within(mobile).queryByRole("link", { name: "Jobs" })).not.toBeInTheDocument();
});

it("shows the canonical commitment status in the project", async () => {
  sources = [{ ...source("commitment"), status: "done" }];
  mount(<ProjectPage params={{ id: "p1" }} />);
  const label = await screen.findByText("Open commitments");
  expect(label.parentElement).toHaveTextContent("0");
  fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
  expect(screen.getByText("Status: done")).toBeInTheDocument();
  await screen.findByText(/Find related emails/);
});

it("refreshes project counts when a commitment is resolved elsewhere in the app", async () => {
  sources = [source("commitment", "c1")];
  function ResolveElsewhere() {
    const { resolve } = useCommitments();
    return <button onClick={() => void resolve("c1")}>Complete commitment elsewhere</button>;
  }
  mount(<><ProjectPage params={{ id: "p1" }} /><ResolveElsewhere /></>);
  const label = await screen.findByText("Open commitments");
  expect(label.parentElement).toHaveTextContent("1");
  fireEvent.click(screen.getByRole("button", { name: "Complete commitment elsewhere" }));
  await waitFor(() => expect(label.parentElement).toHaveTextContent("0"));
});
