import "@testing-library/jest-dom";
import { act, renderHook } from "@testing-library/react";

import { useMeetingCapture } from "@/hooks/useMeetingCapture";

// getFreshAccessToken is the seam the reconnect path can throw on.
jest.mock("@/lib/auth-session", () => ({
  getFreshAccessToken: jest.fn(),
}));
import { getFreshAccessToken } from "@/lib/auth-session";
const mockGetToken = getFreshAccessToken as jest.Mock;

// ---------------------------------------------------------------------------
// Web API fakes (jsdom implements none of these)
// ---------------------------------------------------------------------------

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  binaryType = "";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Record<string, ((ev?: unknown) => void)[]> = {};

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }
  addEventListener(type: string, cb: (ev?: unknown) => void) {
    (this.listeners[type] ||= []).push(cb);
  }
  removeEventListener(type: string, cb: (ev?: unknown) => void) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== cb);
  }

  // --- test drivers ---
  _open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  _message(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  _serverClose() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
  get binarySends() {
    return this.sent.filter((d) => typeof d !== "string");
  }
}

function makeTrack(kind: "audio" | "video") {
  return { kind, stop: jest.fn(), addEventListener: jest.fn() };
}
function makeStream(tracks: ReturnType<typeof makeTrack>[]) {
  return {
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((t) => t.kind === "audio"),
    getVideoTracks: () => tracks.filter((t) => t.kind === "video"),
    removeTrack: (t: unknown) => {
      const i = tracks.indexOf(t as ReturnType<typeof makeTrack>);
      if (i >= 0) tracks.splice(i, 1);
    },
  };
}

class FakeAudioWorkletNode {
  static instances: FakeAudioWorkletNode[] = [];
  port: { onmessage: ((e: { data: ArrayBuffer }) => void) | null } = {
    onmessage: null,
  };
  connect = jest.fn();
  disconnect = jest.fn();
  constructor() {
    FakeAudioWorkletNode.instances.push(this);
  }
}
class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  destination = {};
  audioWorklet = { addModule: jest.fn().mockResolvedValue(undefined) };
  close = jest.fn().mockResolvedValue(undefined);
  constructor() {
    FakeAudioContext.instances.push(this);
  }
  createMediaStreamSource() {
    return { connect: jest.fn(), disconnect: jest.fn() };
  }
  createGain() {
    return { gain: { value: 0 }, connect: jest.fn(), disconnect: jest.fn() };
  }
}

let micTrack: ReturnType<typeof makeTrack>;
let tabAudioTrack: ReturnType<typeof makeTrack>;
let getUserMedia: jest.Mock;
let getDisplayMedia: jest.Mock;

beforeEach(() => {
  jest.useFakeTimers();
  FakeWebSocket.instances = [];
  FakeAudioWorkletNode.instances = [];
  FakeAudioContext.instances = [];

  micTrack = makeTrack("audio");
  tabAudioTrack = makeTrack("audio");
  getUserMedia = jest.fn().mockResolvedValue(makeStream([micTrack]));
  getDisplayMedia = jest
    .fn()
    .mockResolvedValue(makeStream([makeTrack("video"), tabAudioTrack]));

  (global as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
  (global as unknown as { AudioContext: unknown }).AudioContext = FakeAudioContext;
  (global as unknown as { AudioWorkletNode: unknown }).AudioWorkletNode =
    FakeAudioWorkletNode;
  (window as unknown as { AudioWorklet: unknown }).AudioWorklet = function () {};
  (navigator as unknown as { mediaDevices: unknown }).mediaDevices = {
    getUserMedia,
    getDisplayMedia,
  };
  mockGetToken.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

/** Drive begin() → open → ready so the hook reaches a live 'recording' state. */
async function startRecording(result: { current: ReturnType<typeof useMeetingCapture> }) {
  await act(async () => {
    await result.current.begin();
  });
  const ws = FakeWebSocket.instances[0];
  act(() => ws._open());
  act(() => ws._message({ type: "status", status: "ready" }));
  return ws;
}

// ---------------------------------------------------------------------------
// Finding #3 — a throw on the reconnect/token path must not strand live media
// ---------------------------------------------------------------------------

describe("useMeetingCapture reconnect hardening (finding #3)", () => {
  it("releases media and shows an actionable error when token refresh rejects mid-reconnect", async () => {
    mockGetToken
      .mockResolvedValueOnce("tok-initial") // begin()
      .mockRejectedValueOnce(new Error("refresh failed")); // reconnect

    const { result } = renderHook(() => useMeetingCapture("m-1"));
    const ws = await startRecording(result);
    expect(result.current.status).toBe("recording");

    // Socket drops → hook schedules a reconnect (backoff attempt 1 = 1000ms).
    act(() => ws._serverClose());
    expect(result.current.status).toBe("reconnecting");

    // Fire the backoff timer; the forced token refresh rejects.
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1000);
    });

    // (2) terminal, rendered error state — NOT a recording-looking spinner.
    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatch(/reconnect/i);
    // No second socket was opened (connect never ran).
    expect(FakeWebSocket.instances).toHaveLength(1);

    // (1) media fully released: mic + tab tracks stopped, AudioContext closed
    //     → the browser recording indicator goes off.
    expect(micTrack.stop).toHaveBeenCalled();
    expect(tabAudioTrack.stop).toHaveBeenCalled();
    expect(FakeAudioContext.instances[0].close).toHaveBeenCalled();
  });

  it("reconnects normally when the token refresh succeeds", async () => {
    mockGetToken
      .mockResolvedValueOnce("tok-initial")
      .mockResolvedValueOnce("tok-refreshed");

    const { result } = renderHook(() => useMeetingCapture("m-1"));
    const ws = await startRecording(result);

    act(() => ws._serverClose());
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1000);
    });

    // A fresh socket was opened; no error state.
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(result.current.status).toBe("reconnecting"); // waiting on the new socket
    expect(result.current.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Finding #4 — per-channel buffering must not drop audio across a reconnect
// ---------------------------------------------------------------------------

describe("useMeetingCapture send buffer (finding #4)", () => {
  function pushFrames(node: FakeAudioWorkletNode, count: number) {
    act(() => {
      for (let i = 0; i < count; i++) {
        node.port.onmessage?.({ data: new ArrayBuffer(640) });
      }
    });
  }

  it("delivers every buffered frame across a 4s reconnect gap (no drop)", async () => {
    mockGetToken.mockResolvedValue("tok-initial");
    const { result } = renderHook(() => useMeetingCapture("m-1"));

    await act(async () => {
      await result.current.begin();
    });
    // Socket is still CONNECTING, so frames buffer instead of sending.
    const ws = FakeWebSocket.instances[0];
    const [meNode, themNode] = FakeAudioWorkletNode.instances;

    // 4s of audio per channel (attempt-3 backoff window): 200 frames each,
    // 400 combined — the old single 250-frame shared buffer would have dropped
    // 150; the per-channel buffer must keep all of them.
    const FRAMES = 200;
    pushFrames(meNode, FRAMES);
    pushFrames(themNode, FRAMES);

    // Reconnect completes → flush.
    act(() => ws._open());
    act(() => ws._message({ type: "status", status: "ready" }));

    expect(result.current.status).toBe("recording");
    expect(ws.binarySends).toHaveLength(FRAMES * 2); // nothing dropped
  });

  it("does not let one flooded channel evict the other channel's buffered audio", async () => {
    mockGetToken.mockResolvedValue("tok-initial");
    const { result } = renderHook(() => useMeetingCapture("m-1"));

    await act(async () => {
      await result.current.begin();
    });
    const ws = FakeWebSocket.instances[0];
    const [meNode, themNode] = FakeAudioWorkletNode.instances;

    // "them" floods past its own cap; "me" stays small.
    pushFrames(themNode, 700); // > 500 cap → some of THEM's oldest drop
    pushFrames(meNode, 10);

    act(() => ws._open());
    act(() => ws._message({ type: "status", status: "ready" }));

    // With a shared buffer, them's flood would have evicted me's 10 frames.
    // Per channel, all 10 me frames survive (them is capped independently at 500).
    expect(ws.binarySends).toHaveLength(500 + 10);
  });
});
