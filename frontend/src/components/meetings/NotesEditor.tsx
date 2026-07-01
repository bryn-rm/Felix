"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

const DEBOUNCE_MS = 800;

export interface NotesEditorHandle {
  flush: () => Promise<void>;
}

interface NotesEditorProps {
  initialValue: string;
  onSave: (content: string) => void | Promise<void>;
}

/**
 * Debounced notes textarea for the live capture page. Owns its text locally and
 * autosaves `DEBOUNCE_MS` after the last keystroke; flushes a pending save on
 * unmount so nothing is lost when the user hits Stop.
 *
 * Notes are preserved verbatim in the AI summary (origin:'user' blocks), so what
 * the user types here survives into the enhanced notes unchanged.
 */
export const NotesEditor = forwardRef<NotesEditorHandle, NotesEditorProps>(
  function NotesEditor({ initialValue, onSave }, ref) {
    const [value, setValue] = useState(initialValue);
    const [saved, setSaved] = useState(true);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const latestRef = useRef(value);
    const onSaveRef = useRef(onSave);
    const pendingSaveRef = useRef(false);
    // Once the user starts typing we stop accepting late initialValue updates so
    // an async-loaded value can never clobber in-progress edits.
    const dirtyRef = useRef(false);

    useEffect(() => {
      latestRef.current = value;
    }, [value]);
    useEffect(() => {
      onSaveRef.current = onSave;
    }, [onSave]);

    const flushPending = useCallback(async () => {
      if (!pendingSaveRef.current) return;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      await onSaveRef.current(latestRef.current);
      pendingSaveRef.current = false;
      setSaved(true);
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        flush: () => flushPending(),
      }),
      [flushPending],
    );

    // Seed (or re-seed) from a late-arriving initialValue — e.g. saved notes that
    // load after this component mounts on a refresh/reconnect — but never over a
    // value the user has already edited.
    useEffect(() => {
      if (!dirtyRef.current) {
        setValue(initialValue);
        latestRef.current = initialValue;
      }
    }, [initialValue]);

    // Flush any pending save on unmount.
    useEffect(() => {
      return () => {
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = null;
        if (pendingSaveRef.current) {
          void onSaveRef.current(latestRef.current);
        }
      };
    }, []);

    function handleChange(next: string) {
      dirtyRef.current = true;
      pendingSaveRef.current = true;
      setValue(next);
      setSaved(false);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(async () => {
        timerRef.current = null;
        try {
          await onSaveRef.current(latestRef.current);
          pendingSaveRef.current = false;
          setSaved(true);
        } catch {
          // Keep pendingSaveRef set so flush()/unmount retries, and leave the
          // indicator on "Saving…" rather than swallowing the rejection.
          setSaved(false);
        }
      }, DEBOUNCE_MS);
    }

    return (
      <div className="flex h-full flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Your notes
          </label>
          <span className="text-[11px] text-slate-500">
            {saved ? "Saved" : "Saving…"}
          </span>
        </div>
        <textarea
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          placeholder="Jot down what matters — Felix keeps your words verbatim and adds context around them."
          className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
        />
      </div>
    );
  },
);
