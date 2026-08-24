/**
 * Can this client capture a meeting, or can it only observe one?
 *
 * That distinction — not desktop-vs-phone — is what decides where a live
 * meeting should open. It lives here rather than inside `useMeetingCapture` so
 * routing can ask the question without importing the capture pipeline (an
 * AudioWorklet, a WebSocket and two media streams) to answer it.
 */

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
