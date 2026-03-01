/**
 * useVoice — WebSocket voice pipeline for Felix.
 *
 * Manages:
 *   - WebSocket connection with JWT auth (first message)
 *   - MediaRecorder (mic → WebM/Opus chunks → server every 100 ms)
 *   - Web Audio API playback of MP3 chunks from ElevenLabs
 *   - Interrupt handling (stops current TTS, resumes listening)
 *
 * Usage:
 *   const { state, interimTranscript, messages, error, start, stop, interrupt } =
 *     useVoice(supabaseAccessToken)
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
  start: () => Promise<void>;
  stop: () => void;
  interrupt: () => void;
}

// Backend WebSocket URL — converts http(s) → ws(s)
const WS_BASE =
  (process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000").replace(
    /^http/,
    "ws",
  );

export function useVoice(token: string | null): UseVoiceReturn {
  const [state, setState] = useState<VoiceState>("idle");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const nextStartRef = useRef<number>(0); // scheduled playback pointer

  // ------------------------------------------------------------------
  // Cleanup — closes WS and stops MediaRecorder
  // ------------------------------------------------------------------
  const cleanup = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setState("idle");
  }, []);

  // ------------------------------------------------------------------
  // Audio playback via Web Audio API
  // Schedules chunks in sequence using an advancing time pointer so
  // chunks play back-to-back with no gaps or overlaps.
  // ------------------------------------------------------------------
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
      // Malformed chunk — skip silently
    }
  }, []);

  // ------------------------------------------------------------------
  // Start MediaRecorder and pipe chunks to the open WebSocket
  // ------------------------------------------------------------------
  const startRecording = useCallback(
    (ws: WebSocket) => {
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
          recorder.start(100); // 100 ms chunks for low-latency streaming
        })
        .catch(() => {
          setError("Microphone access denied.");
          setState("error");
        });
    },
    [],
  );

  // ------------------------------------------------------------------
  // start() — open WebSocket, auth, then start recording
  // ------------------------------------------------------------------
  const start = useCallback(async () => {
    if (!token) {
      setError("Not authenticated.");
      return;
    }
    if (state !== "idle" && state !== "error") return;

    setError(null);
    setState("connecting");

    // Fresh AudioContext for each session
    audioCtxRef.current?.close().catch(() => {});
    const ctx = new (window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext)();
    audioCtxRef.current = ctx;
    nextStartRef.current = 0;

    const ws = new WebSocket(`${WS_BASE}/voice/stream`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ token }));
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
              setMessages((prev) => [
                ...prev,
                { role: "felix", text: msg.text!, timestamp: new Date() },
              ]);
              setState("speaking");
            }
            break;

          case "audio_complete":
            // Transition back to listening once scheduled audio finishes
            const remaining =
              Math.max(0, nextStartRef.current - ctx.currentTime) * 1000;
            setTimeout(() => {
              if (wsRef.current?.readyState === WebSocket.OPEN) {
                setState("listening");
              }
            }, remaining + 50);
            break;

          case "error":
            setError(msg.message ?? "Voice error.");
            setState("error");
            cleanup();
            break;
        }
      } else if (event.data instanceof ArrayBuffer) {
        await enqueueAudioChunk(event.data);
      }
    };

    ws.onerror = () => {
      setError("WebSocket connection failed.");
      setState("error");
    };

    ws.onclose = () => {
      recorderRef.current?.stop();
    };
  }, [token, state, cleanup, startRecording, enqueueAudioChunk]);

  // ------------------------------------------------------------------
  // stop() — end conversation
  // ------------------------------------------------------------------
  const stop = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "stop_audio" }));
    cleanup();
  }, [cleanup]);

  // ------------------------------------------------------------------
  // interrupt() — cut off Felix mid-sentence, return to listening
  // ------------------------------------------------------------------
  const interrupt = useCallback(() => {
    // Recreate AudioContext to immediately stop all scheduled playback
    audioCtxRef.current?.close().catch(() => {});
    const ctx = new (window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext)();
    audioCtxRef.current = ctx;
    nextStartRef.current = 0;

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
    setState("listening");
  }, []);

  // ------------------------------------------------------------------
  // Cleanup on unmount
  // ------------------------------------------------------------------
  useEffect(() => {
    return () => {
      cleanup();
      audioCtxRef.current?.close().catch(() => {});
    };
  }, [cleanup]);

  return { state, interimTranscript, messages, error, start, stop, interrupt };
}
