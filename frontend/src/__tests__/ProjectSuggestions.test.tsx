import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectSuggestionsPanel } from "@/components/projects/ProjectSuggestions";

const discover = jest.fn();
const resolve = jest.fn();
const mutate = jest.fn();
const resetDismissals = jest.fn();
let state: any;
jest.mock("@/hooks/useProjects", () => ({
  useProjectSuggestions: () => state,
  useProjectActions: () => ({ discoverSources: discover, resolveSuggestion: resolve, resetSuggestionDismissals: resetDismissals }),
}));
jest.mock("@/components/projects/ThreadPreview", () => ({ ThreadPreview: ({ suggestionId }: any) => <div>Thread {suggestionId}</div> }));
const item = { id: "s1", kind: "email_thread", source_id: "thread", title: "Launch review", detail: "Pat", occurred_at: "2026-09-09", preview: "Design review follow-up", explanation: "Follows the linked design review." };

beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(global.crypto, "randomUUID", { value: () => "request-id", configurable: true });
  state = { data: { suggestions: [], last_discovered_at: null }, mutate };
  discover.mockResolvedValue(undefined); resolve.mockResolvedValue(undefined);
});

test("only explicit discovery triggers work, with loading and no-match states", async () => {
  const view = render(<ProjectSuggestionsPanel projectId="p1" />);
  expect(discover).not.toHaveBeenCalled();
  expect(screen.getByText(/Find related emails/)).toBeInTheDocument();
  let finish!: () => void;
  discover.mockReturnValue(new Promise<void>(r => { finish = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Find related items" }));
  expect(screen.getByRole("button", { name: "Finding related items…" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Checking existing Felix sources");
  await act(async () => finish());
  expect(discover).toHaveBeenCalledWith("p1", "request-id", expect.any(AbortSignal));
  state = { ...state, data: { suggestions: [], last_discovered_at: "2026-09-09" } };
  view.rerender(<ProjectSuggestionsPanel projectId="p1" />);
  expect(screen.getByText(/No related items found/)).toBeInTheDocument();
});

test("preview, metadata, accept and dismiss use individual suggestion identity", async () => {
  state.data.suggestions = [item];
  render(<ProjectSuggestionsPanel projectId="p1" />);
  expect(screen.getByText(item.explanation)).toBeInTheDocument();
  expect(screen.getByText(item.preview)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Open thread"));
  expect(screen.getByText("Thread s1")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Add to project"));
  await waitFor(() => expect(resolve).toHaveBeenCalledWith("p1", "s1", "accept"));
  await waitFor(() => expect(screen.getByText("Dismiss")).toBeEnabled());
  fireEvent.click(screen.getByText("Dismiss"));
  await waitFor(() => expect(resolve).toHaveBeenCalledWith("p1", "s1", "dismiss"));
});

test("read errors hide cached private cards and expose retry", () => {
  state = { ...state, data: { suggestions: [item] }, error: new Error("unavailable") };
  render(<ProjectSuggestionsPanel projectId="p1" />);
  expect(screen.queryByText(item.title)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Retry"));
  expect(mutate).toHaveBeenCalled();
});

test("discovery failure permits retry with same request ID", async () => {
  discover.mockRejectedValueOnce(new Error("Discovery timed out. Please retry."));
  render(<ProjectSuggestionsPanel projectId="p1" />);
  fireEvent.click(screen.getByText("Find related items"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Discovery timed out");
  fireEvent.click(screen.getByText("Find related items"));
  await waitFor(() => expect(discover).toHaveBeenCalledTimes(2));
  expect(discover.mock.calls[0][1]).toBe(discover.mock.calls[1][1]);
});

test.each([375, 1280])("controls remain usable at width %i", async width => {
  window.innerWidth = width;
  state.data.suggestions = [{ ...item, kind: "meeting", href: "/meetings/m1" }];
  render(<ProjectSuggestionsPanel projectId="p1" />);
  expect(screen.getByRole("link", { name: "Open source" })).toHaveAttribute("href", "/meetings/m1");
  expect(screen.getByText("Add to project").parentElement).toHaveClass("flex-wrap");
  resolve.mockRejectedValueOnce(new Error("Source unavailable"));
  fireEvent.click(screen.getByText("Add to project"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source unavailable");
  expect(mutate).toHaveBeenCalled();
});


test("stale results explain withholding instead of claiming no matches", () => {
  state.data = { suggestions: [], stale: true, last_discovered_at: "2026-09-09", dismissed_count: 0 };
  render(<ProjectSuggestionsPanel projectId="p1" />);
  expect(screen.getByRole("status")).toHaveTextContent("Saved suggestions are out of date");
  expect(screen.queryByText(/No related items found/)).not.toBeInTheDocument();
});

test("dismissals can be reset without starting discovery", async () => {
  state.data.dismissed_count = 2;
  render(<ProjectSuggestionsPanel projectId="p1" />);
  fireEvent.click(screen.getByText("Reset dismissed items (2)"));
  await waitFor(() => expect(resetDismissals).toHaveBeenCalledWith("p1"));
  expect(discover).not.toHaveBeenCalled();
});

test("client deadline gives a useful timeout message", async () => {
  jest.useFakeTimers();
  try {
    discover.mockImplementation((_id, _request, signal: AbortSignal) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("signal is aborted without reason", "AbortError")));
    }));
    render(<ProjectSuggestionsPanel projectId="p1" />);
    fireEvent.click(screen.getByText("Find related items"));
    await act(async () => { jest.advanceTimersByTime(85_000); });
    expect(screen.getByRole("alert")).toHaveTextContent("Discovery timed out. Please retry.");
  } finally { jest.useRealTimers(); }
});
