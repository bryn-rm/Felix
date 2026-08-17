import "@testing-library/jest-dom";
import { act, renderHook } from "@testing-library/react";

import { useManualAssist } from "@/hooks/useManualAssist";
import { ApiError } from "@/lib/api";

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ...actual, api: { post: jest.fn() } };
});
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

const item = {
  id: "i-1",
  kind: "answer",
  source: "ask",
  question: "What did we decide?",
  title: "Last time",
  body: "You agreed on £40.",
  transcript_ts: null,
  dismissed: false,
  request_id: "req-1",
  created_at: "2026-08-17T10:00:00Z",
};

beforeEach(() => {
  mockPost.mockReset();
});

describe("useManualAssist", () => {
  it("appends the answered item and reports success", async () => {
    mockPost.mockResolvedValue({ item });
    const { result } = renderHook(() => useManualAssist("m-1", true));

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.sendAsk("What did we decide?");
    });

    expect(ok).toBe(true);
    expect(result.current.items).toEqual([item]);
    expect(result.current.askPending).toBe(false);
    expect(result.current.askError).toBeNull();
  });

  it("returns false on failure so the caller keeps the typed question", async () => {
    // The sidebar clears the textarea only on a true return. Resolving true
    // before the POST settled meant a 429 wiped a question the user may have
    // spent a while typing (up to 6000 characters).
    mockPost.mockRejectedValue(new ApiError(429, "Monthly AI usage limit reached"));
    const { result } = renderHook(() => useManualAssist("m-1", true));

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.sendAsk("What did we decide?");
    });

    expect(ok).toBe(false);
    expect(result.current.items).toEqual([]);
    expect(result.current.askError).toMatch(/monthly ai usage limit/i);
    expect(result.current.askPending).toBe(false);
  });

  it("times out a stalled request instead of wedging askPending forever", async () => {
    jest.useFakeTimers();
    // A bare fetch never settles on its own — without the abort the ask box
    // stays disabled until a full page reload, with no error shown.
    let aborted = false;
    mockPost.mockImplementation(
      (_path: string, _body: unknown, options: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    );
    const { result } = renderHook(() => useManualAssist("m-1", true));

    let settled: Promise<boolean>;
    act(() => {
      settled = result.current.sendAsk("What did we decide?");
    });
    expect(result.current.askPending).toBe(true);

    await act(async () => {
      jest.advanceTimersByTime(75_000);
      await settled!;
    });

    expect(aborted).toBe(true);
    expect(result.current.askPending).toBe(false);
    expect(result.current.askError).toMatch(/no answer arrived/i);
    jest.useRealTimers();
  });

  it("refuses a second ask while one is in flight", async () => {
    let release: (value: unknown) => void = () => {};
    mockPost.mockImplementation(() => new Promise((resolve) => (release = resolve)));
    const { result } = renderHook(() => useManualAssist("m-1", true));

    let second: boolean | undefined;
    await act(async () => {
      void result.current.sendAsk("First");
      second = await result.current.sendAsk("Second");
    });

    expect(second).toBe(false);
    expect(mockPost).toHaveBeenCalledTimes(1);

    await act(async () => {
      release({ item });
    });
  });

  it("does nothing when live assist is off", async () => {
    const { result } = renderHook(() => useManualAssist("m-1", false));

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.sendAsk("What did we decide?");
    });

    expect(ok).toBe(false);
    expect(mockPost).not.toHaveBeenCalled();
  });
});
