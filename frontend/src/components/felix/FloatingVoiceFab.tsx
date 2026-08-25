"use client";

/**
 * FloatingVoiceFab — fixed bottom-right Voice Orb on every authenticated page.
 *
 * Tapping it opens the full VoiceModal overlay (same experience as today).
 * Reads its visual state from VoiceContext so it reflects any active session
 * started from the modal or the keyboard shortcut.
 */

import { usePathname } from "next/navigation";

import { useVoiceContext } from "./VoiceContext";
import { VoiceOrb } from "./VoiceOrb";

const LIVE_ASSIST_VIEWER = /^\/meetings\/live\/[^/]+\/viewer$/;

export function FloatingVoiceFab() {
  const { state, openModal, modalOpen } = useVoiceContext();
  const pathname = usePathname();

  // Hide the FAB while the modal is open — the modal already shows its own orb.
  if (modalOpen) return null;
  // …and on the Live Assist viewer, where on a phone it sits directly on top of
  // that page's ask button. Tapping it there would also open a microphone
  // session on a device someone is holding through a meeting another device is
  // recording, which is the last thing that surface should invite.
  if (pathname && LIVE_ASSIST_VIEWER.test(pathname)) return null;

  return (
    <div className="fixed bottom-20 right-5 z-40 md:bottom-6 md:right-6">
      <VoiceOrb state={state} onClick={openModal} size={56} />
    </div>
  );
}
