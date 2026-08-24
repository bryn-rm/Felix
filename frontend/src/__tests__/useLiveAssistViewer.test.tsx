import "@testing-library/jest-dom";
import { act, renderHook } from "@testing-library/react";

// swr is mocked so the polling contract can be asserted directly off the config
// the hook hands it, rather than inferred from wall-clock behaviour.
jest.mock("swr", () => ({ __esModule: true, default: jest.fn() }));
const mockUseAssistAsk = jest.fn();
jest.mock("@/hooks/useAssistAsk", () => ({
  useAssistAsk: (...args: unknown[]) => mockUseAssistAsk(...args),
}));
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({ api: { post: (...a: unknown[]) => mockApiPost(...a) } }));
import useSWR from "swr";

import { VIEWER_POLL_MS, useLiveAssistViewer } from "@/hooks/useLiveAssistViewer";

const mockUseSWR = useSWR as unknown as jest.Mock;

interface SWRCall {
  key: string | null;
  config: { refreshInterval: (latest?: unknown) => number };
}

function callFor(meetingId: string | null, data?: unknown): SWRCall {
  mockUseSWR.mockReturnValue({
    data, error: undefined, isLoading: false, mutate: jest.fn(),
  });
  renderHook(() => useLiveAssistViewer(meetingId));
  const [key, , config] = mockUseSWR.mock.calls.at(-1);
  return { key, config };
}

beforeEach(() => {
  mockUseSWR.mockReset();
  mockUseAssistAsk.mockReset();
  mockUseAssistAsk.mockReturnValue({
    items: [], sendAsk: jest.fn(), askPending: false, askError: null,
  });
  mockApiPost.mockReset();
  mockApiPost.mockResolvedValue({});
});

describe("useLiveAssistViewer", () => {
  it("reads the dedicated viewer snapshot, not the full meeting detail", () => {
    // GET /meetings/{id} carries the whole transcript; a 3s poll must not.
    expect(callFor("m-1").key).toBe("/meetings/m-1/live-view");
  });

  it("does not fetch without a meeting id", () => {
    expect(callFor(null).key).toBeNull();
  });

  it("polls while the capturing device is still recording", () => {
    const { config } = callFor("m-1");
    expect(config.refreshInterval({ meeting: { status: "recording" } })).toBe(
      VIEWER_POLL_MS,
    );
  });

  it("stops polling once the meeting is no longer recording", () => {
    // Read-only after the end: a finished meeting must not leave a phone
    // polling in a pocket.
    const { config } = callFor("m-1");
    for (const status of ["processing", "done", "error", "idle"]) {
      expect(config.refreshInterval({ meeting: { status } })).toBe(0);
    }
    expect(config.refreshInterval(undefined)).toBe(0);
  });

  it("exposes polled state plus the two interactions the phone owns", () => {
    mockUseSWR.mockReturnValue({
      data: {
        meeting: { id: "m-1", title: "Roadmap", status: "recording" },
        items: [{ id: "i-1" }],
        capture_attached: true,
      },
      error: undefined,
      isLoading: false,
      mutate: jest.fn(),
    });
    const { result } = renderHook(() => useLiveAssistViewer("m-1"));

    expect(result.current.live).toBe(true);
    expect(result.current.items).toHaveLength(1);
    expect(result.current.captureAttached).toBe(true);
    // Ask and dismiss — and nothing that owns the meeting: no lifecycle, no
    // capture, no transcript.
    expect(Object.keys(result.current).sort()).toEqual(
      [
        "askError", "askPending", "captureAttached", "dismiss", "error",
        "isLoading", "items", "live", "meeting", "sendAsk",
      ].sort(),
    );
  });

  it("reports capture liveness as unknown until the snapshot lands", () => {
    // Rendering "recording device disconnected" off missing data would invent a
    // problem out of a slow first fetch.
    mockUseSWR.mockReturnValue({
      data: undefined, error: undefined, isLoading: true, mutate: jest.fn(),
    });
    const { result } = renderHook(() => useLiveAssistViewer("m-1"));

    expect(result.current.captureAttached).toBeNull();
  });

  it("passes the capturing device's dropped socket straight through", () => {
    mockUseSWR.mockReturnValue({
      data: {
        meeting: { id: "m-1", status: "recording" },
        items: [],
        capture_attached: false,
      },
      error: undefined,
      isLoading: false,
      mutate: jest.fn(),
    });
    const { result } = renderHook(() => useLiveAssistViewer("m-1"));

    // Server state, not a UI inference about how the page looks.
    expect(result.current.captureAttached).toBe(false);
    expect(result.current.live).toBe(true);
  });

  it("reports an ended meeting as not live", () => {
    mockUseSWR.mockReturnValue({
      data: { meeting: { id: "m-1", status: "done" }, items: [] },
      error: undefined,
      isLoading: false,
      mutate: jest.fn(),
    });
    const { result } = renderHook(() => useLiveAssistViewer("m-1"));

    expect(result.current.live).toBe(false);
  });

  it("lets a server dismissal remove a locally appended answer", () => {
    const localAnswer = {
      id: "ask-1", kind: "answer", source: "ask", title: "Pricing",
      body: "You agreed £40.", dismissed: false,
    };
    let snapshot = {
      meeting: { id: "m-1", status: "recording" },
      items: [] as typeof localAnswer[],
      capture_attached: true,
    };
    mockUseAssistAsk.mockReturnValue({
      items: [localAnswer], sendAsk: jest.fn(), askPending: false, askError: null,
    });
    mockUseSWR.mockImplementation(() => ({
      data: snapshot, error: undefined, isLoading: false, mutate: jest.fn(),
    }));
    const { result, rerender } = renderHook(() => useLiveAssistViewer("m-1"));

    // Immediate REST response fills the gap before the polling snapshot lands.
    expect(result.current.items.map((item) => item.id)).toEqual(["ask-1"]);

    snapshot = { ...snapshot, items: [localAnswer] };
    rerender();
    expect(result.current.items.map((item) => item.id)).toEqual(["ask-1"]);

    // A server copy wins ID dedupe, so its shared dismissal flag prevents the
    // local undismissed copy from adding the card back.
    snapshot = {
      ...snapshot,
      items: [{ ...localAnswer, dismissed: true }],
    };
    rerender();
    expect(result.current.items).toEqual([]);
  });

  it("does not POST a dismissal without a meeting id", async () => {
    // Only the SWR key guards on the nullable id. Unguarded, the URL would
    // interpolate "null", 404, and roll the optimistic hide back — the card
    // reappearing with nothing shown to explain it.
    mockUseSWR.mockReturnValue({
      data: undefined, error: undefined, isLoading: false, mutate: jest.fn(),
    });
    const { result } = renderHook(() => useLiveAssistViewer(null));

    await act(async () => {
      await result.current.dismiss("item-1");
    });

    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it("forgets dismissals when the hook moves to another meeting", async () => {
    // The instance survives a client-side navigation between two live
    // meetings; a retained set would filter the new meeting's cards by the
    // previous one's ids.
    const card = {
      id: "shared-id", kind: "answer", source: "ask", title: "Pricing",
      body: "You agreed £40.", dismissed: false,
    };
    mockUseSWR.mockImplementation(() => ({
      data: {
        meeting: { id: "m-1", status: "recording" },
        items: [card],
        capture_attached: true,
      },
      error: undefined, isLoading: false, mutate: jest.fn(),
    }));
    const { result, rerender } = renderHook(
      ({ id }) => useLiveAssistViewer(id),
      { initialProps: { id: "m-1" } },
    );

    await act(async () => {
      await result.current.dismiss("shared-id");
    });
    expect(result.current.items).toEqual([]);

    rerender({ id: "m-2" });
    expect(result.current.items.map((item) => item.id)).toEqual(["shared-id"]);
  });
});
