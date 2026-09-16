import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig, useSWRConfig } from "swr";
import { api } from "@/lib/api";
import { ProjectAskPanel } from "@/components/projects/ProjectAsk";
import type { ProjectAnswerResult } from "@/hooks/useProjects";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
const navigate = jest.fn();
const openThread = jest.fn();
let result: ProjectAnswerResult;
const saved = (): ProjectAnswerResult => ({ stale: false, withheld: false, answer: {
  request_id: "saved-request", question: "Why did launch move?", generated_at: "2026-09-16T12:00:00Z", omitted_count: 7,
  claims: [{ kind: "answer", text: "The team postponed launch for certification.", citations: [{ evidence_id: "email:sent:1", quote: "Wait for certification" }] },
    { kind: "conflict", text: "The email and confirmed scope give different dates.", citations: [{ evidence_id: "email:sent:1", quote: "Wait for certification" }] }],
  unanswered: ["Who approved the change?"],
} });
function Reload() {
  const { mutate } = useSWRConfig();
  return <button onClick={() => void mutate("/projects/p1/ask")}>Refresh answer</button>;
}
function mount() {
  return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, errorRetryCount: 0 }}>
    <ProjectAskPanel projectId="p1" navigate={navigate} openThread={openThread} /><Reload />
  </SWRConfig>);
}
function enterQuestion(value = "Why did launch move?") {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value } });
}

beforeEach(() => {
  jest.clearAllMocks();
  let id = 0;
  Object.defineProperty(global.crypto, "randomUUID", { configurable: true, value: jest.fn(() => `attempt-${++id}`) });
  result = { answer: null, stale: false, withheld: false };
  (api.get as jest.Mock).mockImplementation(async (url: string) => {
    if (url.endsWith("/ask")) return { ...result };
    if (url.includes("/evidence?")) return { id: "email:sent:1", kind: "email", text: "Wait for certification before launch", href: null, section: "Sources", record_id: "thread-1" };
    throw new Error(`Unexpected ${url}`);
  });
  (api.post as jest.Mock).mockImplementation(async (_url, body) => { result = saved(); result.answer!.request_id = body.request_id; return result; });
});

it("asks only on submit and presents cited answers, conflicts and missing evidence", async () => {
  mount();
  await screen.findByText(/Try asking what was decided/);
  expect(api.post).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Ask project" })).toBeDisabled();
  enterQuestion("  Why did launch move?  ");
  fireEvent.click(screen.getByRole("button", { name: "Ask project" }));
  expect(await screen.findByText("The team postponed launch for certification.")).toBeInTheDocument();
  expect(screen.getByText("Conflicting evidence")).toBeInTheDocument();
  expect(screen.getByText("Missing evidence")).toBeInTheDocument();
  expect(screen.getByText("Who approved the change?")).toBeInTheDocument();
  expect(screen.getByText(/7 evidence items/)).toBeInTheDocument();
  expect(screen.getByLabelText("Your question")).toHaveValue("");
  expect(api.post).toHaveBeenCalledWith("/projects/p1/ask", { question: "Why did launch move?", request_id: "attempt-1" }, { signal: expect.any(AbortSignal) });
});

it("revalidates citations and opens a sent-only thread", async () => {
  result = saved();
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Evidence 1.1" }));
  const dialog = screen.getByRole("dialog");
  expect(await within(dialog).findByText("Wait for certification")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Open thread" }));
  expect(openThread).toHaveBeenCalledWith("thread-1");
  expect(api.get).toHaveBeenCalledWith("/projects/p1/evidence?key=email%3Asent%3A1");
});

it("reuses an attempt after failure but uses a fresh ID for a changed question", async () => {
  result = saved();
  (api.post as jest.Mock).mockRejectedValue(new Error("Could not answer. Retry."));
  mount();
  await screen.findByText("The team postponed launch for certification.");
  enterQuestion();
  fireEvent.click(screen.getByRole("button", { name: "Ask project" }));
  await screen.findByText("Could not answer. Retry.");
  expect(screen.getByLabelText("Your question")).toHaveValue("Why did launch move?");
  expect(screen.getByText("The team postponed launch for certification.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ask project" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  await screen.findByText("Could not answer. Retry.");
  expect((api.post as jest.Mock).mock.calls[0][1]).toEqual((api.post as jest.Mock).mock.calls[1][1]);
  enterQuestion("What remains open?");
  fireEvent.click(screen.getByRole("button", { name: "Ask project" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(3));
  await screen.findByText("Could not answer. Retry.");
  expect((api.post as jest.Mock).mock.calls[2][1].request_id).toBe("attempt-2");
});

it("refreshes a stale saved answer with a new request", async () => {
  result = { ...saved(), stale: true };
  mount();
  expect(await screen.findByText(/This answer may be out of date/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/projects/p1/ask", { question: "Why did launch move?", request_id: "attempt-1" }, { signal: expect.any(AbortSignal) }));
  await waitFor(() => expect(screen.queryByText(/This answer may be out of date/)).not.toBeInTheDocument());
});

it.each(["withheld", "error"])("hides cached prose and an open citation when validation returns %s", async (outcome) => {
  result = saved();
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Evidence 1.1" }));
  await within(screen.getByRole("dialog")).findByText("Wait for certification");
  if (outcome === "withheld") result = { answer: null, stale: true, withheld: true, question: "Why did launch move?" };
  else (api.get as jest.Mock).mockRejectedValue(new Error("Access unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh answer" }));
  await screen.findByText(outcome === "withheld" ? /saved answer is withheld/ : /Could not verify the saved answer/);
  expect(screen.queryByText("The team postponed launch for certification.")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  if (outcome === "withheld") expect(screen.getByText("Why did launch move?")).toBeInTheDocument();
});

it("makes missing evidence explicit when no answer is supported", async () => {
  result = saved(); result.answer!.claims = [];
  mount();
  expect(await screen.findByText("There is not enough evidence to answer this question.")).toBeInTheDocument();
  expect(screen.getByText("Who approved the change?")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Evidence/ })).not.toBeInTheDocument();
});

it("prevents duplicate submissions while answering and aborts on unmount", async () => {
  let finish!: (value: ProjectAnswerResult) => void;
  (api.post as jest.Mock).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const view = mount();
  enterQuestion();
  fireEvent.click(screen.getByRole("button", { name: "Ask project" }));
  expect(screen.getByRole("button", { name: "Answering…" })).toBeDisabled();
  expect(screen.getByLabelText("Your question")).toBeDisabled();
  expect(api.post).toHaveBeenCalledTimes(1);
  const signal = (api.post as jest.Mock).mock.calls[0][2].signal as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { finish(saved()); });
});
