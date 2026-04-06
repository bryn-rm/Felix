"use client";

/**
 * VoiceContext — single shared voice session for the entire authenticated app.
 *
 * Wraps the useVoice() WebSocket pipeline once and exposes:
 *   - voice state, messages, interim transcript
 *   - start / stop / interrupt
 *   - openModal / closeModal (for the fullscreen VoiceModal overlay)
 *
 * Both the floating VoiceOrb FAB and the VoiceModal read from this context,
 * so the same WebSocket session is shared rather than duplicated.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { useVoice, type VoiceMessage, type VoiceState } from "@/hooks/useVoice";
import { supabase } from "@/lib/supabase";

interface VoiceContextValue {
  // Session state
  state: VoiceState;
  messages: VoiceMessage[];
  interimTranscript: string;
  error: string | null;
  // Controls
  start: () => void;
  stop: () => void;
  interrupt: () => void;
  // Modal
  modalOpen: boolean;
  openModal: () => void;
  closeModal: () => void;
  // Token (for components that need it directly, e.g. modal)
  token: string | null;
}

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function VoiceProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // Load and track Supabase access token
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setToken(session?.access_token ?? null);
    });
    const { data: listener } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setToken(session?.access_token ?? null);
      },
    );
    return () => listener.subscription.unsubscribe();
  }, []);

  const voice = useVoice(token);

  const openModal = useCallback(() => {
    if (token) setModalOpen(true);
  }, [token]);

  const closeModal = useCallback(() => {
    setModalOpen(false);
  }, []);

  // Cmd+Shift+F / Ctrl+Shift+F opens the full VoiceModal anywhere
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "F") {
        e.preventDefault();
        openModal();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [openModal]);

  const value = useMemo<VoiceContextValue>(
    () => ({
      state: voice.state,
      messages: voice.messages,
      interimTranscript: voice.interimTranscript,
      error: voice.error,
      start: voice.start,
      stop: voice.stop,
      interrupt: voice.interrupt,
      modalOpen,
      openModal,
      closeModal,
      token,
    }),
    [voice, modalOpen, openModal, closeModal, token],
  );

  return (
    <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>
  );
}

export function useVoiceContext(): VoiceContextValue {
  const ctx = useContext(VoiceContext);
  if (!ctx) {
    throw new Error("useVoiceContext must be used within a VoiceProvider");
  }
  return ctx;
}
