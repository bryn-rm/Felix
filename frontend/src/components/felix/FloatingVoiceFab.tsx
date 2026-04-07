"use client";

/**
 * FloatingVoiceFab — fixed bottom-right Voice Orb on every authenticated page.
 *
 * Tapping it opens the full VoiceModal overlay (same experience as today).
 * Reads its visual state from VoiceContext so it reflects any active session
 * started from the modal or the keyboard shortcut.
 */

import { useVoiceContext } from "./VoiceContext";
import { VoiceOrb } from "./VoiceOrb";

export function FloatingVoiceFab() {
  const { state, openModal, modalOpen } = useVoiceContext();

  // Hide the FAB while the modal is open — the modal already shows its own orb.
  if (modalOpen) return null;

  return (
    <div className="fixed bottom-20 right-5 z-40 md:bottom-6 md:right-6">
      <VoiceOrb state={state} onClick={openModal} size={56} />
    </div>
  );
}
