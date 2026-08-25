"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Check, Copy, Smartphone, X } from "lucide-react";

import { liveAssistViewerPath } from "./constants";

/**
 * "View Live Assist on phone" — the laptop-to-phone handoff.
 *
 * The whole friction this removes is typing a URL with a UUID in it on a phone
 * mid-meeting. What the QR encodes is Felix's ordinary authenticated viewer
 * route, absolute against this tab's own origin: no share token, no meeting
 * secret, nothing that grants access by possession. A phone that isn't signed
 * in gets Felix's normal sign-in and is returned to this meeting afterwards;
 * one signed in as somebody else gets the same 404 it would get by typing the
 * URL.
 *
 * The link is shown in full beside the QR because a QR is useless on a laptop
 * with no second camera to hand, and because a user should be able to see where
 * a code is about to send them.
 */
export function PhoneHandoff({
  meetingId,
  manualNotes = false,
}: {
  meetingId: string;
  manualNotes?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  // Only the browser knows the origin, and it must be absolute for the QR to be
  // scannable — so this is resolved after mount rather than during render.
  const [url, setUrl] = useState("");
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    setUrl(`${window.location.origin}${liveAssistViewerPath(meetingId)}`);
  }, [meetingId]);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Reset between openings so a stale tick isn't the first thing shown.
  useEffect(() => {
    if (!open) setCopied(false);
  }, [open]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // Clipboard access can be refused (insecure origin, permissions). The
      // link is already on screen and selectable, so say nothing and let the
      // user copy it by hand rather than claiming a copy that didn't happen.
      setCopied(false);
    }
  }, [url]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm font-medium text-slate-300 transition-colors hover:border-slate-500"
      >
        <Smartphone className="h-4 w-4" />
        View on phone
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="View Live Assist on phone"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-sm rounded-xl border border-white/[0.06] bg-[#0d1526] p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">
                  View Live Assist on phone
                </h2>
                <p className="mt-1 text-xs text-slate-400">
                  {manualNotes
                    ? "Scan with your phone camera. This manual-notes meeting isn’t being recorded."
                    : "Scan with your phone camera. Recording stays on this device."}
                </p>
              </div>
              <button
                ref={closeRef}
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="rounded p-1.5 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* White plate: a QR needs light quiet-zone margins to scan
                reliably, which the dark panel behind it can't provide. */}
            <div className="mt-4 flex justify-center rounded-lg bg-white p-3">
              {url && (
                <QRCodeSVG
                  value={url}
                  size={192}
                  // The spec's four-module quiet zone, inside the SVG rather
                  // than relying on the plate's padding to supply it.
                  marginSize={4}
                  level="M"
                  title="Live Assist viewer link"
                />
              )}
            </div>

            <label
              htmlFor="phone-handoff-url"
              className="mt-4 block text-[11px] font-semibold uppercase tracking-wider text-slate-500"
            >
              Or open this link
            </label>
            <div className="mt-1.5 flex items-center gap-2">
              <input
                id="phone-handoff-url"
                readOnly
                value={url}
                onFocus={(e) => e.currentTarget.select()}
                className="min-w-0 flex-1 rounded-lg border border-slate-700/60 bg-slate-800/40 px-3 py-2 text-xs text-slate-300"
              />
              <button
                onClick={copy}
                aria-label="Copy link"
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-indigo-500"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>

            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              You&rsquo;ll be asked to sign in to Felix on the phone if you
              aren&rsquo;t already. The link only works for your own account.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
