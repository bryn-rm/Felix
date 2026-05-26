"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

// Public link to the external access-request form (Tally/Google Form/etc.). When
// unset the "Request access" link is hidden entirely — fail closed, never render a
// dead link.
const REQUEST_ACCESS_URL = process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL;

function oauthErrorMessage(code: string): string {
  // Match on the provider reason rather than an exact code: the precise value
  // Supabase forwards for a non-test-user / declined consent is logged in
  // /auth/callback and should be confirmed against a live denied flow.
  if (/denied/i.test(code)) {
    return "We couldn't sign you in — your Google account may not be on the invite list yet. You can request access below.";
  }
  if (code === "missing_code") {
    return "Sign-in didn't complete. Please try again.";
  }
  return "Something went wrong signing you in. Please try again.";
}

const features = [
  { label: "INBOX", desc: "Triages and prioritises so you only see what matters" },
  { label: "VOICE", desc: "Drafts replies that sound like you, not a robot" },
  { label: "BRIEFING", desc: "A morning digest of your day — meetings, action items, context" },
];

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Surface an OAuth error forwarded by /auth/callback (e.g. ?error=access_denied),
  // then strip it from the URL so it doesn't persist on refresh or when shared.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("error");
    if (!code) return;
    setError(oauthErrorMessage(code));
    params.delete("error");
    const query = params.toString();
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}`,
    );
  }, []);

  async function handleSignIn() {
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });
    if (error) {
      setError(error.message);
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex flex-col lg:flex-row">
      {/* ── LEFT PANEL ── */}
      <div
        className="relative flex flex-col justify-center px-8 py-16 lg:py-0 lg:px-20 lg:w-[55%] overflow-hidden"
        style={{ backgroundColor: "var(--felix-bg-deep)" }}
      >
        {/* Animated mesh gradient */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{ opacity: 0.15 }}
        >
          <div
            className="absolute w-[800px] h-[800px] rounded-full"
            style={{
              top: "-20%",
              left: "-10%",
              background:
                "radial-gradient(circle, #4f46e5 0%, #1e1b4b 40%, transparent 70%)",
              animation: "meshMove 20s ease-in-out infinite",
            }}
          />
          <div
            className="absolute w-[600px] h-[600px] rounded-full"
            style={{
              bottom: "-15%",
              right: "-5%",
              background:
                "radial-gradient(circle, #3730a3 0%, #0c1445 40%, transparent 70%)",
              animation: "meshMove 20s ease-in-out infinite reverse",
              animationDelay: "-7s",
            }}
          />
        </div>

        <div className="relative z-10 max-w-xl">
          {/* Logo + wordmark */}
          <div className="flex items-center gap-3.5 mb-14 felix-fade-up felix-delay-0">
            <img
              src="/icon-512.png"
              alt="Felix icon"
              width={52}
              height={52}
              className="rounded-xl shadow-lg shadow-indigo-500/20 flex-shrink-0"
            />
            <span
              className="text-[2rem] tracking-tight leading-none"
              style={{
                fontFamily: "'Instrument Serif', serif",
                color: "var(--felix-text-bright)",
              }}
            >
              Felix
            </span>
          </div>

          {/* Editorial headline */}
          <h1
            className="mb-6 felix-fade-up felix-delay-1"
            style={{
              fontFamily: "'Instrument Serif', serif",
              fontSize: "clamp(2.8rem, 5vw, 4.5rem)",
              lineHeight: 1.05,
              color: "var(--felix-text-bright)",
              letterSpacing: "-0.02em",
            }}
          >
            Your inbox,
            <br />
            under control.
          </h1>

          {/* Subtext */}
          <p
            className="mb-14 max-w-md felix-fade-up felix-delay-2"
            style={{
              color: "var(--felix-text-muted)",
              fontSize: "1rem",
              lineHeight: 1.7,
            }}
          >
            Felix reads your email, drafts replies in your voice,
            and briefs you every morning.
          </p>

          {/* Feature rows */}
          <div className="space-y-5 felix-fade-up felix-delay-3">
            {features.map((f) => (
              <div
                key={f.label}
                className="flex items-start gap-4 pl-4"
                style={{ borderLeft: "2px solid var(--felix-accent)" }}
              >
                <span
                  className="flex-shrink-0 text-[11px] font-semibold tracking-[0.2em] mt-0.5"
                  style={{ color: "var(--felix-accent)", minWidth: "68px" }}
                >
                  {f.label}
                </span>
                <span
                  className="text-sm leading-relaxed"
                  style={{ color: "var(--felix-text-muted)" }}
                >
                  {f.desc}
                </span>
              </div>
            ))}
          </div>

          {/* Bottom stamp */}
          <p
            className="mt-20 text-xs tracking-wide felix-fade-up felix-delay-4"
            style={{ color: "var(--felix-text-ghost)" }}
          >
            Invite only · Private by design
          </p>
        </div>
      </div>

      {/* ── RIGHT PANEL ── */}
      <div
        className="flex items-center justify-center px-8 py-16 lg:py-0 lg:w-[45%]"
        style={{ backgroundColor: "var(--felix-bg-panel)" }}
      >
        <div className="w-full max-w-sm felix-fade-up felix-delay-5">
          {/* Eyebrow */}
          <p
            className="text-[11px] font-semibold tracking-[0.2em] mb-4"
            style={{ color: "var(--felix-accent)" }}
          >
            SECURE SIGN IN
          </p>

          {/* Heading */}
          <h2
            className="text-[28px] font-medium mb-6"
            style={{ color: "var(--felix-text-heading)" }}
          >
            Welcome back
          </h2>

          {/* Divider */}
          <div
            className="mb-8"
            style={{
              height: "1px",
              background:
                "linear-gradient(to right, var(--felix-accent-dim), transparent)",
            }}
          />

          {error && (
            <p className="mb-6 rounded-lg bg-red-900/40 border border-red-700/50 px-4 py-3 text-sm text-red-300">
              {error}
            </p>
          )}

          {/* Google Sign-In */}
          <button
            onClick={handleSignIn}
            disabled={loading}
            className="felix-google-btn flex items-center justify-center gap-3 w-full rounded-xl bg-white disabled:opacity-60 disabled:cursor-wait"
            style={{ padding: "14px 20px" }}
          >
            <svg width="20" height="20" viewBox="0 0 18 18" aria-hidden>
              <path
                fill="#4285F4"
                d="M16.51 8H8.98v3h4.3c-.18 1-.74 1.48-1.6 2.04v2.01h2.6a7.8 7.8 0 0 0 2.38-5.88c0-.57-.05-.66-.15-1.18z"
              />
              <path
                fill="#34A853"
                d="M8.98 17c2.16 0 3.97-.72 5.3-1.94l-2.6-2a4.8 4.8 0 0 1-7.18-2.54H1.83v2.07A8 8 0 0 0 8.98 17z"
              />
              <path
                fill="#FBBC05"
                d="M4.5 10.52a4.8 4.8 0 0 1 0-3.04V5.41H1.83a8 8 0 0 0 0 7.18l2.67-2.07z"
              />
              <path
                fill="#EA4335"
                d="M8.98 4.18c1.17 0 2.23.4 3.06 1.2l2.3-2.3A8 8 0 0 0 1.83 5.4L4.5 7.49a4.77 4.77 0 0 1 4.48-3.31z"
              />
            </svg>
            <span
              style={{
                color: "#1a1a2e",
                fontSize: "15px",
                fontWeight: 500,
              }}
            >
              {loading ? "Redirecting…" : "Continue with Google"}
            </span>
          </button>

          {/* Trust signals */}
          <div
            className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2 mt-6"
            style={{ color: "var(--felix-text-dim)", fontSize: "12px" }}
          >
            <span className="flex items-center gap-1.5">
              <span style={{ fontSize: "11px" }}>🔒</span> End-to-end encrypted
            </span>
            <span className="flex items-center gap-1.5">
              <span style={{ fontSize: "11px" }}>👁</span> Read permissions only
            </span>
            <span className="flex items-center gap-1.5">
              <span style={{ fontSize: "11px" }}>✕</span> Cancel anytime
            </span>
          </div>

          {/* Request access — only shown when an external form URL is configured */}
          {REQUEST_ACCESS_URL && (
            <p
              className="mt-6 text-center text-xs"
              style={{ color: "var(--felix-text-dim)" }}
            >
              Not on the invite list yet?{" "}
              <a
                href={REQUEST_ACCESS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2"
                style={{ color: "var(--felix-accent)" }}
              >
                Request access
              </a>
            </p>
          )}

          {/* Footer */}
          <p
            className="mt-16 text-center text-xs"
            style={{ color: "var(--felix-text-footer)" }}
          >
            © Felix 2026
          </p>
        </div>
      </div>
    </main>
  );
}
