/**
 * useMeetingCapture — browser two-channel capture for Meeting Capture (Phase 8).
 *
 * Option A: capture both sides of an in-browser meeting locally.
 *   • "Me"   = the user's microphone (getUserMedia).
 *   • "Them" = the shared tab's audio (getDisplayMedia → drop the video track).
 *
 * Each stream feeds an AudioWorklet that downsamples to 16 kHz mono LINEAR16 and
 * posts Int16 PCM frames. We prefix every frame with a channel byte (0x00 me /
 * 0x01 them) and ship it over the meeting WebSocket. Interim + final transcripts
 * come back over the same socket for live display.
 *
 * Reuses the useVoice.ts patterns: buildWsBase() off NEXT_PUBLIC_API_URL (NOT
 * NEXT_PUBLIC_BACKEND_URL — see FIX 1 in useVoice.ts), token-as-first-message
 * auth, tokenRef for reconnect, intentional-close + mounted guards.
 *
 * Chrome-only: no ScriptProcessor fallback (see the worklet + §3.5). Gate on
 * `isMeetingCaptureSupported()` before rendering the start control.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getFreshAccessToken } from "@/lib/auth-session";

export type CaptureStatus =
  | "idle"
  | "requesting" // acquiring mic / tab permissions
  | "connecting" // opening the WebSocket
  | "recording"
  | "reconnecting"
  | "stopping" // sent stop; waiting for the server to flush + persist the tail
  | "ended"
  | "error";

export interface LiveLine {
  speaker: "me" | "them";
  text: string;
  ts_start: number;
}

interface UseMeetingCaptureReturn {
  status: CaptureStatus;
  error: string | null;
  liveTranscript: LiveLine[];
  interim: { me: string; them: string };
  begin: () => Promise<void>;
  /** Resolves once the server has flushed + persisted the final STT segments. */
  stop: () => Promise<void>;
  /**
   * Terminal-failure sink: release media and settle in a rendered 'error' state
   * with a retry affordance. Exposed so callers that own a post-capture step
   * (e.g. the live page's REST /end) can funnel a real failure through the SAME
   * sink instead of dead-ending the user on a blank recording-status page.
   */
  failCapture: (message: string) => void;
}

const CHANNEL_BYTE: Record<"me" | "them", number> = { me: 0x00, them: 0x01 };
const MAX_RECONNECT = 3;
// Each channel emits one 20ms PCM frame → 50 frames/sec. Buffer PER CHANNEL (not
// one shared buffer — a loud channel must never evict the other's audio) enough
// to cover the worst reconnect gap without dropping a frame: the longest backoff
// is 2^(MAX_RECONNECT-1)s (=4s) plus token refresh + WS handshake. 10s of
// headroom per channel covers that comfortably (~640 KB/channel worst case).
const FRAMES_PER_SEC_PER_CHANNEL = 50;
const MAX_BUFFER_SECONDS = 10;
const MAX_BUFFERED_FRAMES_PER_CHANNEL =
  FRAMES_PER_SEC_PER_CHANNEL * MAX_BUFFER_SECONDS; // 500 frames ≈ 10s per channel
const PING_INTERVAL_MS = 20_000;
// How long to wait for the server to drain Google STT + persist the last
// segments (it closes the socket when done) before forcing the close so the UI
// can't hang on a stuck connection.
const STOP_DRAIN_TIMEOUT_MS = 6_000;
const WORKLET_URL = "/meeting-capture-worklet.js";

// ---------------------------------------------------------------------------
// Build WS base from NEXT_PUBLIC_API_URL (matches api.ts / useVoice.ts FIX 1).
// ---------------------------------------------------------------------------
function buildWsBase(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  if (apiUrl) return apiUrl.replace(/^http/, "ws");
  if (typeof window !== "undefined") {
    return window.location.origin.replace(/^http/, "ws");
  }
  return "ws://localhost:8000";
}

/** Chrome-first capability gate: needs AudioWorklet + getDisplayMedia + getUserMedia. */
export function isMeetingCaptureSupported(): boolean {
  if (typeof window === "undefined") return false;
  return (
    typeof window.AudioWorklet !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getDisplayMedia &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

interface CaptureOptions {
  /** Called when the user stops sharing the tab (the "them" track ends). */
  onShareEnded?: () => void;
}

export function useMeetingCapture(
  meetingId: string | null,
  options?: CaptureOptions,
): UseMeetingCaptureReturn {
  const [status, setStatus] = useState<CaptureStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [liveTranscript, setLiveTranscript] = useState<LiveLine[]>([]);
  const [interim, setInterim] = useState<{ me: string; them: string }>({
    me: "",
    them: "",
  });

  const wsRef = useRef<WebSocket | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const tabStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const nodesRef = useRef<
    {
      source: MediaStreamAudioSourceNode;
      worklet: AudioWorkletNode;
      silence: GainNode;
    }[]
  >([]);

  const tokenRef = useRef<string | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectCountRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  // Outgoing frames produced before the socket is ready (or during a reconnect)
  // are buffered here, PER CHANNEL, oldest-dropped past the cap so memory stays
  // bounded and one channel can't evict the other's audio across a reconnect.
  const sendBufferRef = useRef<{ me: ArrayBuffer[]; them: ArrayBuffer[] }>({
    me: [],
    them: [],
  });

  // Keep the latest onShareEnded without re-wiring track listeners.
  const onShareEndedRef = useRef<CaptureOptions["onShareEnded"]>(
    options?.onShareEnded,
  );
  useEffect(() => {
    onShareEndedRef.current = options?.onShareEnded;
  }, [options?.onShareEnded]);

  // connect closure kept in a ref so the onclose reconnect timer never calls a
  // stale version (mirrors useVoice's connectRef).
  const connectRef = useRef<(() => void) | null>(null);

  // -------------------------------------------------------------------------
  // Frame plumbing
  // -------------------------------------------------------------------------
  const flushBuffer = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const buf = sendBufferRef.current;
    // Per channel, in FIFO order (order within a channel is what STT cares about;
    // the two channels demux to independent server-side streams).
    for (const frame of buf.me) ws.send(frame);
    for (const frame of buf.them) ws.send(frame);
    buf.me = [];
    buf.them = [];
  }, []);

  const sendFrame = useCallback((speaker: "me" | "them", pcm: ArrayBuffer) => {
    // Prefix the PCM with the channel byte the backend demuxes on.
    const framed = new Uint8Array(pcm.byteLength + 1);
    framed[0] = CHANNEL_BYTE[speaker];
    framed.set(new Uint8Array(pcm), 1);

    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(framed.buffer);
    } else {
      const buf = sendBufferRef.current[speaker];
      buf.push(framed.buffer);
      if (buf.length > MAX_BUFFERED_FRAMES_PER_CHANNEL) buf.shift();
    }
  }, []);

  // -------------------------------------------------------------------------
  // Teardown helpers
  // -------------------------------------------------------------------------
  const cancelReconnect = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const stopPing = useCallback(() => {
    if (pingTimerRef.current !== null) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  }, []);

  const teardownMedia = useCallback(() => {
    nodesRef.current.forEach(({ source, worklet, silence }) => {
      worklet.port.onmessage = null;
      source.disconnect();
      worklet.disconnect();
      silence.disconnect();
    });
    nodesRef.current = [];
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    tabStreamRef.current?.getTracks().forEach((t) => t.stop());
    tabStreamRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
  }, []);

  const teardownAll = useCallback(() => {
    cancelReconnect();
    stopPing();
    teardownMedia();
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
    sendBufferRef.current = { me: [], them: [] };
  }, [cancelReconnect, stopPing, teardownMedia]);

  // Terminal failure sink. Every unrecoverable path funnels here so the outcome
  // is always the same: (1) block further reconnects, (2) fully release media —
  // mic + tab tracks stopped and the AudioContext closed, so the browser's
  // recording indicator goes OFF — and (3) settle in a rendered 'error' state
  // the UI shows with a retry, never a recording-looking limbo.
  const failCapture = useCallback(
    (message: string) => {
      intentionalCloseRef.current = true;
      teardownAll();
      setError(message);
      setStatus("error");
    },
    [teardownAll],
  );

  // -------------------------------------------------------------------------
  // WebSocket
  // -------------------------------------------------------------------------
  const connect = useCallback(() => {
    const token = tokenRef.current;
    if (!token || !meetingId) {
      setError("Not authenticated.");
      setStatus("error");
      return;
    }

    const ws = new WebSocket(`${buildWsBase()}/ws/meetings/${meetingId}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ token }));
    };

    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return; // server never sends binary
      let msg: {
        type: string;
        status?: string;
        speaker?: "me" | "them";
        text?: string;
        is_final?: boolean;
        ts_start?: number;
        message?: string;
      };
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      switch (msg.type) {
        case "status":
          if (msg.status === "ready") {
            // The user may have stopped while this (re)connect was still opening;
            // don't resurrect the session back into 'recording'.
            if (intentionalCloseRef.current) break;
            reconnectCountRef.current = 0;
            ws.send(JSON.stringify({ type: "start" }));
            flushBuffer();
            setStatus("recording");
          } else if (msg.status === "channel_stopped") {
            // One channel's live transcription died server-side; the other may
            // still be healthy, so surface it without ending the capture.
            setError(
              "Live transcription for one side stopped. The rest is still being recorded.",
            );
          }
          break;

        case "transcript": {
          const speaker = msg.speaker === "them" ? "them" : "me";
          const text = msg.text ?? "";
          if (msg.is_final) {
            setInterim((prev) => ({ ...prev, [speaker]: "" }));
            if (text.trim()) {
              setLiveTranscript((prev) => [
                ...prev,
                { speaker, text: text.trim(), ts_start: msg.ts_start ?? 0 },
              ]);
            }
          } else {
            setInterim((prev) => ({ ...prev, [speaker]: text }));
          }
          break;
        }

        case "pong":
          break;

        case "error":
          failCapture(msg.message ?? "Capture error.");
          break;
      }
    };

    // Reconnect logic lives entirely in onclose (mirrors useVoice).
    ws.onerror = () => {};

    ws.onclose = () => {
      stopPing();
      if (
        !intentionalCloseRef.current &&
        reconnectCountRef.current < MAX_RECONNECT &&
        mountedRef.current
      ) {
        reconnectCountRef.current += 1;
        const delay = Math.pow(2, reconnectCountRef.current - 1) * 1000;
        setStatus("reconnecting");
        reconnectTimerRef.current = setTimeout(async () => {
          // The WHOLE reconnect sequence (token refresh + connect + any await
          // added to this path later) runs under one guard. On ANY throw we
          // release media and land in a rendered 'error' state — never a
          // recording-looking limbo with live mic/tab tracks and nothing being
          // transcribed. This is the structural invariant: a future await added
          // here is covered automatically, so the leak can't be reintroduced.
          try {
            if (!mountedRef.current || intentionalCloseRef.current) return;
            // Refresh the token — the original may have expired mid-meeting.
            const token = await getFreshAccessToken({ forceRefresh: true });
            // The user may have stopped (or the component unmounted) while the
            // refresh was in flight — re-check before opening a new socket so we
            // never orphan a backend STT session after stop().
            if (!mountedRef.current || intentionalCloseRef.current) return;
            tokenRef.current = token;
            connectRef.current?.();
          } catch {
            // Unmount and user-stop already own teardown on their own paths;
            // otherwise fail closed to a released + actionable error state.
            if (mountedRef.current && !intentionalCloseRef.current) {
              failCapture(
                "Couldn’t reconnect. The recording was saved up to this point.",
              );
            }
          }
        }, delay);
      } else if (!intentionalCloseRef.current) {
        setError("Connection lost. The recording was saved up to this point.");
        setStatus("error");
      }
    };

    // Heartbeat so an idle socket isn't dropped by an intermediary.
    stopPing();
    pingTimerRef.current = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "ping" }));
      }
    }, PING_INTERVAL_MS);
  }, [meetingId, flushBuffer, stopPing, failCapture]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // -------------------------------------------------------------------------
  // Media graph
  // -------------------------------------------------------------------------
  const wireChannel = useCallback(
    (ctx: AudioContext, stream: MediaStream, speaker: "me" | "them") => {
      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, "meeting-capture-processor");
      const silence = ctx.createGain();
      silence.gain.value = 0;
      worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        sendFrame(speaker, e.data);
      };
      source.connect(worklet);
      worklet.connect(silence);
      silence.connect(ctx.destination);
      nodesRef.current.push({ source, worklet, silence });
    },
    [sendFrame],
  );

  const begin = useCallback(async () => {
    if (!meetingId) {
      setError("No meeting to record.");
      setStatus("error");
      return;
    }
    if (!isMeetingCaptureSupported()) {
      setError("Meeting capture needs Google Chrome on desktop.");
      setStatus("error");
      return;
    }
    if (status !== "idle" && status !== "error") return;

    setError(null);
    setLiveTranscript([]);
    setInterim({ me: "", them: "" });
    intentionalCloseRef.current = false;
    reconnectCountRef.current = 0;
    setStatus("requesting");

    // 1. Microphone → "Me". Keep echoCancellation on to reduce channel bleed.
    let micStream: MediaStream;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: false,
      });
    } catch {
      setError("Microphone access was denied.");
      setStatus("error");
      return;
    }
    micStreamRef.current = micStream;

    // 2. Tab share → "Them". The user must pick a tab AND tick "share tab audio".
    let tabStream: MediaStream;
    try {
      tabStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
      });
    } catch {
      micStream.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
      setError("Tab sharing was cancelled.");
      setStatus("error");
      return;
    }

    // We only need the audio track — drop the video to avoid encoding it.
    tabStream.getVideoTracks().forEach((t) => {
      t.stop();
      tabStream.removeTrack(t);
    });
    if (tabStream.getAudioTracks().length === 0) {
      micStream.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
      tabStream.getTracks().forEach((t) => t.stop());
      setError(
        'No tab audio was shared. Re-share and tick "Also share tab audio".',
      );
      setStatus("error");
      return;
    }
    tabStreamRef.current = tabStream;

    // Tab closed / "Stop sharing" clicked → finalize gracefully.
    tabStream.getAudioTracks()[0].addEventListener("ended", () => {
      onShareEndedRef.current?.();
    });

    // 3. Build the audio graph and load the worklet.
    try {
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      await ctx.audioWorklet.addModule(WORKLET_URL);
      wireChannel(ctx, micStream, "me");
      wireChannel(ctx, tabStream, "them");
    } catch {
      teardownMedia();
      setError("Could not start audio processing.");
      setStatus("error");
      return;
    }

    // 4. Open the socket (frames produced now buffer until "ready").
    setStatus("connecting");
    try {
      tokenRef.current = await getFreshAccessToken();
    } catch {
      // Without this, a token failure would leave the mic + tab tracks live
      // (recording indicator stuck on) and the UI frozen on "connecting".
      teardownMedia();
      setError("Could not authenticate. Please try again.");
      setStatus("error");
      return;
    }
    connect();
  }, [meetingId, status, wireChannel, teardownMedia, connect]);

  // -------------------------------------------------------------------------
  // stop() — user-initiated teardown. Does NOT call REST /end; the page owns
  // that so it can navigate afterwards. WS drop alone never ends the meeting.
  //
  // Critically, this resolves only once the server has *closed the socket*,
  // which it does after its `await stt.stop()` finishes draining Google STT and
  // persisting the final segments. The page must await this before calling REST
  // /end, otherwise summarization can spawn before the tail of the transcript is
  // written and permanently miss the end of the meeting.
  // -------------------------------------------------------------------------
  const stop = useCallback((): Promise<void> => {
    return new Promise<void>((resolve) => {
      intentionalCloseRef.current = true;
      cancelReconnect();
      stopPing();
      // Stop capturing immediately — but send {stop} only after, so every frame
      // already produced is queued ahead of it and gets fed to STT server-side.
      teardownMedia();
      setStatus("stopping");

      const ws = wsRef.current;
      if (
        !ws ||
        ws.readyState === WebSocket.CLOSED ||
        ws.readyState === WebSocket.CLOSING
      ) {
        wsRef.current = null;
        sendBufferRef.current = { me: [], them: [] };
        setStatus("ended");
        resolve();
        return;
      }

      let settled = false;
      let timer: ReturnType<typeof setTimeout>;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        ws.removeEventListener("close", finish);
        wsRef.current = null;
        sendBufferRef.current = { me: [], them: [] };
        setStatus("ended");
        resolve();
      };

      const sendStop = () => {
        // Deliver any frames buffered during a reconnect before signalling end,
        // so the tail of the meeting isn't dropped (stop()'s core contract).
        flushBuffer();
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "stop" }));
        }
      };

      // The server closes the socket once it has drained + persisted finals.
      ws.addEventListener("close", finish);
      // Fallback so a stuck server can't hang the UI forever.
      timer = setTimeout(() => {
        try {
          ws.close();
        } catch {
          /* already closing */
        }
        finish();
      }, STOP_DRAIN_TIMEOUT_MS);

      if (ws.readyState === WebSocket.OPEN) {
        sendStop();
      } else {
        // A reconnect socket is still opening: flush the buffered tail + stop
        // once it connects (bounded by the drain timeout) rather than discarding.
        ws.addEventListener("open", sendStop, { once: true });
      }
    });
  }, [cancelReconnect, stopPing, teardownMedia, flushBuffer]);

  // -------------------------------------------------------------------------
  // Unmount cleanup
  // -------------------------------------------------------------------------
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      intentionalCloseRef.current = true;
      teardownAll();
    };
  }, [teardownAll]);

  return { status, error, liveTranscript, interim, begin, stop, failCapture };
}
