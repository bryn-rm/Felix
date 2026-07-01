/**
 * meeting-capture-worklet.js — Phase 8 (browser capture, Option A).
 *
 * One AudioWorkletProcessor per capture channel ("me" mic / "them" tab audio).
 * It downsamples the graph's native rate (typically 44.1 kHz / 48 kHz) to the
 * 16 kHz LINEAR16 mono PCM the backend STT expects, and posts fixed-size Int16
 * frames back to the main thread, which prefixes each with its channel byte and
 * ships it over the WebSocket.
 *
 * Chrome-only: AudioWorklet has no ScriptProcessorNode fallback here by design
 * (the deprecated ScriptProcessor path is intentionally not implemented — see
 * the §3.5 "Chrome-first" constraint). The hook gates on AudioWorklet support
 * before loading this module.
 *
 * `sampleRate` is a global in the AudioWorkletGlobalScope (the context's rate).
 */

const TARGET_RATE = 16000;
// 20 ms of audio at 16 kHz. Small enough for low latency, large enough to keep
// per-frame WS overhead down.
const FRAME_SAMPLES = 320;

class MeetingCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / TARGET_RATE; // e.g. 48000/16000 = 3.0
    this._buffer = []; // pending input samples (source rate)
    this._readPos = 0; // fractional cursor into _buffer
    this._out = []; // resampled Int16 samples awaiting a full frame
  }

  process(inputs) {
    const input = inputs[0];
    const channel = input && input[0];
    if (!channel || channel.length === 0) {
      // No input this quantum (e.g. track muted) — keep the processor alive.
      return true;
    }

    // Accumulate incoming samples (mono — first channel only).
    for (let i = 0; i < channel.length; i++) {
      this._buffer.push(channel[i]);
    }

    // Resample to 16 kHz with linear interpolation, carrying the fractional
    // cursor across process() calls so no samples are dropped or duplicated.
    while (this._readPos + 1 < this._buffer.length) {
      const idx = Math.floor(this._readPos);
      const frac = this._readPos - idx;
      const sample =
        this._buffer[idx] * (1 - frac) + this._buffer[idx + 1] * frac;
      // Float [-1, 1] → Int16.
      const clamped = sample < -1 ? -1 : sample > 1 ? 1 : sample;
      this._out.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      this._readPos += this._ratio;
    }

    // Drop the input samples we've fully consumed; keep the tail. Clamp to the
    // buffer length: at non-integer ratios (e.g. 44.1kHz) the cursor can advance
    // past the end, and subtracting an unclamped floor would leave _readPos one
    // sample short of the retained tail — a slow drift that re-reads samples.
    const consumed = Math.min(Math.floor(this._readPos), this._buffer.length);
    if (consumed > 0) {
      this._buffer.splice(0, consumed);
      this._readPos -= consumed;
    }

    // Emit complete frames as transferable Int16 buffers.
    while (this._out.length >= FRAME_SAMPLES) {
      const frame = Int16Array.from(this._out.splice(0, FRAME_SAMPLES));
      this.port.postMessage(frame.buffer, [frame.buffer]);
    }

    return true;
  }
}

registerProcessor("meeting-capture-processor", MeetingCaptureProcessor);
