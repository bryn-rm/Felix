import "@testing-library/jest-dom";
import { act, renderHook, waitFor } from "@testing-library/react";

// swr is mocked so the revalidation contract can be read off the config the
// hook hands it, rather than inferred from wall-clock behaviour.
jest.mock("swr", () => ({ __esModule: true, default: jest.fn() }));
const mockApiPost = jest.fn();
const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...a: unknown[]) => mockApiGet(...a),
    post: (...a: unknown[]) => mockApiPost(...a),
  },
}));
import useSWR from "swr";

import { ASSIST_RECONCILE_MS, useAssistItems } from "@/hooks/useAssistItems";
import type { AssistItem } from "@/lib/types";

const mockUseSWR = useSWR as unknown as jest.Mock;

function item(overrides: Partial<AssistItem> & { id: string }): AssistItem {
  return {
    kind: "fact",
    source: "proactive",
    question: null,
    title: `Card ${overrides.id}`,
    body: "…",
    transcript_ts: null,
    dismissed: false,
    request_id: null,
    expansion_options: [],
    created_at: "2026-08-24T10:00:00Z",
    ...overrides,
  };
}

function render(
  persisted: AssistItem[],
  liveItems: AssistItem[] = [],
  options: { enabled?: boolean; live?: boolean } = {},
) {
  const mutate = jest.fn();
  mockUseSWR.mockReturnValue({ data: { items: persisted }, mutate });
  const view = renderHook(() => useAssistItems("m-1", liveItems, options));
  const [key, , config] = mockUseSWR.mock.calls.at(-1);
  return { ...view, key, config, mutate };
}

beforeEach(() => {
  mockUseSWR.mockReset();
  mockApiPost.mockReset();
  mockApiPost.mockResolvedValue({});
  mockApiGet.mockReset();
});

describe("useAssistItems — revalidation cadence", () => {
  it("reconciles on a modest interval while the meeting is open", () => {
    const { key, config } = render([], [], { live: true });

    expect(key).toBe("/meetings/m-1/assist");
    expect(config.refreshInterval).toBe(ASSIST_RECONCILE_MS);
  });

  it("stops once the meeting is no longer open to assist writes", () => {
    // Nothing can change on the other device any more, so nothing needs
    // re-reading — an ended meeting must not leave a tab polling.
    expect(render([], [], { live: false }).config.refreshInterval).toBe(0);
  });

  it("is slower than the phone's own poll, not a copy of it", () => {
    // The phone is watching a live stream; this device is catching up on the
    // occasional out-of-band change with a transcript socket already open.
    expect(ASSIST_RECONCILE_MS).toBeGreaterThanOrEqual(10_000);
  });

  it("never fetches at all when live assist is off", () => {
    // Fail closed: without the flag the endpoint 404s.
    expect(render([], [], { enabled: false, live: true }).key).toBeNull();
  });
});

describe("useAssistItems — reconciling the other device's changes", () => {
  it("shows an answer the phone asked for, which never touched this socket", () => {
    const { result } = render([item({ id: "phone-1", source: "ask" })], [], {
      live: true,
    });

    expect(result.current.items.map((i) => i.id)).toEqual(["phone-1"]);
  });

  it("removes a card the phone dismissed, even though the socket delivered it", () => {
    // The persisted row is authoritative in both directions: `dismissed: true`
    // has to beat this device's own live copy, or the two devices disagree
    // about what is still on screen.
    const live = [item({ id: "i-1" })];
    const { result } = render([item({ id: "i-1", dismissed: true })], live, {
      live: true,
    });

    expect(result.current.items).toEqual([]);
  });

  it("does not duplicate an item that arrived over the socket first", () => {
    const live = [item({ id: "i-1", title: "From the socket" })];
    const { result } = render([item({ id: "i-1", title: "From the server" })], live, {
      live: true,
    });

    expect(result.current.items).toHaveLength(1);
    // Persisted content wins the dedupe — one canonical version of a card.
    expect(result.current.items[0].title).toBe("From the server");
  });

  it("keeps showing a socket item the next snapshot hasn't caught up with", () => {
    // Immediate delivery is unchanged: a card is on screen the moment the
    // socket carries it, not at the next revalidation.
    const { result } = render([], [item({ id: "ws-1" })], { live: true });

    expect(result.current.items.map((i) => i.id)).toEqual(["ws-1"]);
  });

  it("restores canonical state after a revalidation, without resurrecting cards", () => {
    const { result, rerender } = render([item({ id: "i-1" })], [], { live: true });
    expect(result.current.items).toHaveLength(1);

    // A later snapshot — the phone dismissed it in between.
    mockUseSWR.mockReturnValue({
      data: { items: [item({ id: "i-1", dismissed: true })] },
      mutate: jest.fn(),
    });
    rerender();

    expect(result.current.items).toEqual([]);
  });
});

describe("useAssistItems — dismissing from this device", () => {
  it("hides optimistically, then persists against the shared row", async () => {
    const { result, mutate } = render([item({ id: "i-1" })], [], { live: true });

    await act(async () => {
      await result.current.dismiss("i-1");
    });

    expect(mockApiPost).toHaveBeenCalledWith("/meetings/m-1/assist/i-1/dismiss", {});
    expect(mutate).toHaveBeenCalled();
    expect(result.current.items).toEqual([]);
  });

  it("puts the card back when the write fails", async () => {
    mockApiPost.mockRejectedValue(new Error("offline"));
    const { result } = render([item({ id: "i-1" })], [], { live: true });

    await act(async () => {
      await result.current.dismiss("i-1");
    });

    await waitFor(() => expect(result.current.items).toHaveLength(1));
  });
});
