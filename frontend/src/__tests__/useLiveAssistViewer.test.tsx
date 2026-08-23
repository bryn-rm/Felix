import "@testing-library/jest-dom";
import { renderHook } from "@testing-library/react";

// swr is mocked so the polling contract can be asserted directly off the config
// the hook hands it, rather than inferred from wall-clock behaviour.
jest.mock("swr", () => ({ __esModule: true, default: jest.fn() }));
import useSWR from "swr";

import { VIEWER_POLL_MS, useLiveAssistViewer } from "@/hooks/useLiveAssistViewer";

const mockUseSWR = useSWR as unknown as jest.Mock;

interface SWRCall {
  key: string | null;
  config: { refreshInterval: (latest?: unknown) => number };
}

function callFor(meetingId: string | null, data?: unknown): SWRCall {
  mockUseSWR.mockReturnValue({ data, error: undefined, isLoading: false });
  renderHook(() => useLiveAssistViewer(meetingId));
  const [key, , config] = mockUseSWR.mock.calls.at(-1);
  return { key, config };
}

beforeEach(() => {
  mockUseSWR.mockReset();
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

  it("exposes read-only state and no mutations", () => {
    mockUseSWR.mockReturnValue({
      data: {
        meeting: { id: "m-1", title: "Roadmap", status: "recording" },
        items: [{ id: "i-1" }],
      },
      error: undefined,
      isLoading: false,
    });
    const { result } = renderHook(() => useLiveAssistViewer("m-1"));

    expect(result.current.live).toBe(true);
    expect(result.current.items).toHaveLength(1);
    // The viewer owns nothing: no dismiss, no ask, no lifecycle.
    expect(Object.keys(result.current).sort()).toEqual(
      ["error", "isLoading", "items", "live", "meeting"].sort(),
    );
  });

  it("reports an ended meeting as not live", () => {
    mockUseSWR.mockReturnValue({
      data: { meeting: { id: "m-1", status: "done" }, items: [] },
      error: undefined,
      isLoading: false,
    });
    const { result } = renderHook(() => useLiveAssistViewer("m-1"));

    expect(result.current.live).toBe(false);
  });
});
