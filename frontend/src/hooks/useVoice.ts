/**
 * useVoice — WebSocket voice pipeline for Felix.
 *
 * Manages:
 *   - WebSocket connection with JWT auth (first message after open)
 *   - MediaRecorder (mic → WebM/Opus chunks → server every 100 ms)
 *   - Web Audio API playback of MP3 chunks from ElevenLabs
 *   - Interrupt handling (stops current TTS, resumes listening)
 *   - Automatic reconnect on unexpected close (≤3 attempts, exponential back-off)
 *
 * Usage:
 *   const { state, interimTranscript, messages, error, start, stop, interrupt } =
 *     useVoice(supabaseAccessToken)
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * CHANGE LOG (with reasons)
 * ─────────────────────────────────────────────────────────────────────────────
 * FIX 1 — Wrong env variable (NEXT_PUBLIC_BACKEND_URL → NEXT_PUBLIC_API_URL)
 *   The rest of the app (api.ts) reads NEXT_PUBLIC_API_URL. Using a different
 *   variable in production would point the WebSocket at localhost:8000 silently.
 *   Also added a window.location.origin fallback for same-origin deployments
 *   where NEXT_PUBLIC_API_URL is intentionally left empty.
 *
 * FIX 2 — Audio interruption on new response
 *   When a `response_text` message arrives while Felix is already speaking,
 *   the previous AudioContext is closed immediately, cancelling all scheduled
 *   audio. Without this, old audio would bleed into the new response.
 *
 * FIX 3 — Reconnect with exponential back-off (up to MAX_RECONNECT attempts)
 *   ws.onclose previously only stopped the recorder. Now it schedules a retry
 *   (1 s → 2 s → 4 s) unless the close was intentional (user called stop())
 *   or the component has unmounted.
 *
 * FIX 4 — intentionalCloseRef
 *   Distinguishes user-initiated stops from unexpected WebSocket drops so that
 *   stop() does not trigger a reconnect attempt.
 *
 * FIX 5 — mountedRef
 *   Prevents a reconnect timer from firing after the component unmounts, which
 *   would cause a setState-on-unmounted-component warning and a dangling WS.
 * ─────────────────────────────────────────────────────────────────────────────
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error";

export interface VoiceMessage {
  role: "user" | "felix";
  text: string;
  timestamp: Date;
}

interface UseVoiceReturn {
  state: VoiceState;
  interimTranscript: string;
  messages: VoiceMessage[];
  error: string | null;
  start: () => void;
  stop: () => void;
  interrupt: () => void;
}

// ---------------------------------------------------------------------------
// FIX 1 — Build WS base URL from NEXT_PUBLIC_API_URL (matches api.ts).
// Handles three cases:
//   • URL provided  → replace http(s) with ws(s)
//   • Empty string  → same-origin deployment, use window.location.origin
//   • SSR / no window → last-resort localhost fallback for dev
// ---------------------------------------------------------------------------
function buildWsBase(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  if (apiUrl) return apiUrl.replace(/^http/, "ws");
  if (typeof window !== "undefined") {
    return window.location.origin.replace(/^http/, "ws");
  }
  return "ws://localhost:8000";
}

const MAX_RECONNECT = 3; // maximum automatic reconnect attempts

// ---------------------------------------------------------------------------
// Helper — create (or recreate) the AudioContext.
// Closing the old context cancels ALL scheduled/playing audio sources
// immediately, which is the correct way to implement audio interruption.
// ---------------------------------------------------------------------------
type AudioContextCtor = typeof AudioContext;

function makeAudioContext(): AudioContext {
  const Ctor: AudioContextCtor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: AudioContextCtor })
      .webkitAudioContext;
  return new Ctor();
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useVoice(token: string | null): UseVoiceReturn {
  const [state, setState] = useState<VoiceState>("idle");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const nextStartRef = useRef<number>(0);

  // FIX 3 / 4 / 5 — reconnect tracking refs
  const reconnectCountRef = useRef(0);
  const intentionalCloseRef = useRef(false); // FIX 4
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true); // FIX 5

  // Keep token in a ref so the ws.onclose closure always reads the latest value
  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  // Keep connect in a ref so the ws.onclose timer can call the latest version
  // without capturing a stale closure.
  const connectRef = useRef<(() => void) | null>(null);

  // -------------------------------------------------------------------------
  // resetAudio — close current AudioContext and open a fresh one.
  // Called on session start, new response arrival, and user interrupt.
  // FIX 2 relies on this to cancel in-progress playback.
  // -------------------------------------------------------------------------
  function resetAudio(): AudioContext {
    audioCtxRef.current?.close().catch(() => {});
    const ctx = makeAudioContext();
    audioCtxRef.current = ctx;
    nextStartRef.current = 0;
    return ctx;
  }

  // -------------------------------------------------------------------------
  // cancelReconnect — clear any pending back-off timer
  // -------------------------------------------------------------------------
  const cancelReconnect = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  // -------------------------------------------------------------------------
  // cleanup — stop mic + close socket without touching reconnect flags
  // -------------------------------------------------------------------------
  const cleanup = useCallback(() => {
    cancelReconnect();
    recorderRef.current?.stop();
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setState("idle");
  }, [cancelReconnect]);

  // -------------------------------------------------------------------------
  // enqueueAudioChunk — schedules MP3 data for gapless sequential playback
  // -------------------------------------------------------------------------
  const enqueueAudioChunk = useCallback(async (data: ArrayBuffer) => {
    const ctx = audioCtxRef.current;
    if (!ctx) return;
    try {
      const buf = await ctx.decodeAudioData(data.slice(0));
      const startAt = Math.max(ctx.currentTime, nextStartRef.current);
      const source = ctx.createBufferSource();
      source.buffer = buf;
      source.connect(ctx.destination);
      source.start(startAt);
      nextStartRef.current = startAt + buf.duration;
    } catch {
      // Malformed or stale chunk (e.g. after AudioContext was reset) — skip
    }
  }, []);

  // -------------------------------------------------------------------------
  // startRecording — opens mic and sends 100ms chunks over the WebSocket
  // -------------------------------------------------------------------------
  const startRecording = useCallback((ws: WebSocket) => {
    navigator.mediaDevices
      .getUserMedia({ audio: true, video: false })
      .then((stream) => {
        streamRef.current = stream;
        const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm";
        const recorder = new MediaRecorder(stream, { mimeType });
        recorderRef.current = recorder;
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            ws.send(e.data);
          }
        };
        recorder.start(100);
      })
      .catch(() => {
        setError("Microphone access denied.");
        setState("error");
      });
  }, []);

  // -------------------------------------------------------------------------
  // connect — opens the WebSocket, wires all handlers.
  // Called by start() and by the reconnect timer.
  // -------------------------------------------------------------------------
  const connect = useCallback(() => {
    const currentToken = tokenRef.current;
    if (!currentToken) {
      setError("Not authenticated.");
      setState("error");
      return;
    }

    setState("connecting");
    resetAudio(); // fresh AudioContext for every session / reconnect

    const ws = new WebSocket(`${buildWsBase()}/voice/stream`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    // Auth token as first message — query params appear in server logs.
    ws.onopen = () => {
      ws.send(JSON.stringify({ token: currentToken }));
    };

    ws.onmessage = async (event) => {
      if (typeof event.data === "string") {
        const msg = JSON.parse(event.data) as {
          type: string;
          text?: string;
          final?: boolean;
          message?: string;
        };

        switch (msg.type) {
          case "ready":
            // Successful auth resets the reconnect counter
            reconnectCountRef.current = 0;
            setState("listening");
            startRecording(ws);
            break;

          case "transcript":
            if (msg.final && msg.text) {
              setInterimTranscript("");
              setMessages((prev) => [
                ...prev,
                { role: "user", text: msg.text!, timestamp: new Date() },
              ]);
              setState("thinking");
            } else if (msg.text) {
              setInterimTranscript(msg.text);
            }
            break;

          case "response_text":
            if (msg.text) {
              // FIX 2 — cancel any in-progress or scheduled audio before the
              // new response plays. Recreating AudioContext stops everything
              // instantly; the old context's decodeAudioData calls will fail
              // silently inside enqueueAudioChunk's catch block.
              resetAudio();

              setMessages((prev) => [
                ...prev,
                { role: "felix", text: msg.text!, timestamp: new Date() },
              ]);
              setState("speaking");
            }
            break;

          case "audio_complete": {
            // Wait for any queued audio to finish, then return to listening.
            // Use audioCtxRef.current so we read the freshest context after
            // any potential FIX 2 reset.
            const remaining =
              Math.max(
                0,
                nextStartRef.current -
                  (audioCtxRef.current?.currentTime ?? 0),
              ) * 1000;
            setTimeout(() => {
              if (wsRef.current?.readyState === WebSocket.OPEN) {
                setState("listening");
              }
            }, remaining + 50);
            break;
          }

          case "error":
            setError(msg.message ?? "Voice error.");
            setState("error");
            // intentional = true so onclose does not attempt reconnect
            intentionalCloseRef.current = true;
            cleanup();
            break;
        }
      } else if (event.data instanceof ArrayBuffer) {
        await enqueueAudioChunk(event.data);
      }
    };

    // FIX 3 — Do NOT set error state here; ws.onclose always fires after
    // ws.onerror and is the single place that decides whether to retry.
    ws.onerror = () => {
      // Intentionally empty — reconnect logic is in onclose
    };

    // FIX 3 — Reconnect with exponential back-off on unexpected close.
    ws.onclose = () => {
      recorderRef.current?.stop();
      recorderRef.current = null;

      if (
        !intentionalCloseRef.current && // FIX 4 — user didn't call stop()
        reconnectCountRef.current < MAX_RECONNECT &&
        mountedRef.current // FIX 5 — component still mounted
      ) {
        reconnectCountRef.current += 1;
        const delay = Math.pow(2, reconnectCountRef.current - 1) * 1000; // 1s, 2s, 4s
        setState("connecting");
        reconnectTimerRef.current = setTimeout(() => {
          if (mountedRef.current && connectRef.current) {
            connectRef.current();
          }
        }, delay);
      } else if (!intentionalCloseRef.current) {
        // Exhausted all retries
        setError("Connection lost. Please try again.");
        setState("error");
      }
    };
  }, [cleanup, enqueueAudioChunk, startRecording]);

  // Keep connectRef pointing at the latest connect closure so the
  // onclose → setTimeout path never calls a stale version.
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // -------------------------------------------------------------------------
  // start() — public entry point; resets session state and opens connection
  // -------------------------------------------------------------------------
  const start = useCallback(() => {
    if (!token) {
      setError("Not authenticated.");
      return;
    }
    if (state !== "idle" && state !== "error") return;

    setError(null);
    setMessages([]);
    setInterimTranscript("");
    intentionalCloseRef.current = false; // FIX 4 — allow reconnect
    reconnectCountRef.current = 0; // FIX 3 — reset counter for fresh session

    connect();
  }, [token, state, connect]);

  // -------------------------------------------------------------------------
  // stop() — ends the session intentionally; prevents reconnect
  // -------------------------------------------------------------------------
  const stop = useCallback(() => {
    intentionalCloseRef.current = true; // FIX 4 — suppress reconnect
    cancelReconnect();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop_audio" }));
    }
    cleanup();
  }, [cancelReconnect, cleanup]);

  // -------------------------------------------------------------------------
  // interrupt() — cuts off Felix mid-sentence and returns to listening
  // -------------------------------------------------------------------------
  const interrupt = useCallback(() => {
    // FIX 2 — same resetAudio technique: closes AudioContext, kills playback
    resetAudio();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
    setState("listening");
  }, []);

  // -------------------------------------------------------------------------
  // Unmount cleanup
  // -------------------------------------------------------------------------
  useEffect(() => {
    mountedRef.current = true; // FIX 5
    return () => {
      mountedRef.current = false; // FIX 5 — block reconnect timer
      intentionalCloseRef.current = true; // FIX 4 — no reconnect on unmount
      cancelReconnect();
      cleanup();
      audioCtxRef.current?.close().catch(() => {});
    };
  }, [cleanup, cancelReconnect]);

  return { state, interimTranscript, messages, error, start, stop, interrupt };
}
