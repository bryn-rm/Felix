import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { loginUrlFor, safeReturnPath } from "@/lib/return-to";
import { ConnectPageClient } from "./page-client";

type ConnectPageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

function resolveOauthErrorMessage(error: string | null): string | null {
  switch (error) {
    case "oauth_expired":
      return "Connection timed out before Google redirected back. Please try again.";
    case "oauth_invalid_state":
      return "This Google connect attempt is no longer valid. Please try again.";
    case "google_denied":
      return "Google access was not granted. Please try again if you still want to connect.";
    case "missing_code":
      return "Google did not return an authorization code. Please try again.";
    case "missing_refresh_token":
      return "Google did not return a refresh token. Disconnect any existing consent and try again.";
    case "token_exchange_failed":
      return "Google token exchange failed. Please try again.";
    case "userinfo_failed":
      return "Felix could not verify your Google account details. Please try again.";
    case "unknown_error":
      return "Google connection failed. Please try again.";
    default:
      return null;
  }
}

async function isGoogleConnected(accessToken: string): Promise<boolean> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "";
  try {
    const res = await fetch(`${apiBase}/auth/google/status`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store",
    });
    if (!res.ok) return false;
    const status = (await res.json()) as { connected?: boolean };
    return status.connected === true;
  } catch {
    return false;
  }
}

export default async function ConnectPage({ searchParams }: ConnectPageProps) {
  // Resolve the destination before either auth guard so every redirect can
  // carry it. It remains untrusted and is revalidated at each hop.
  const resolvedParams = (await searchParams) ?? {};
  const errorParam = resolvedParams.error;
  const errorCode = Array.isArray(errorParam) ? errorParam[0] : errorParam;
  const nextParam = resolvedParams.next;
  const returnTo = safeReturnPath(
    Array.isArray(nextParam) ? nextParam[0] : nextParam,
  );

  const cookieStore = cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(
          cookiesToSet: Array<{ name: string; value: string; options?: CookieOptions }>,
        ) {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        },
      },
    },
  );

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    redirect(loginUrlFor(returnTo));
  }

  // If the backend redirected here with ?error=..., always render the page so
  // the user sees the remediation message — even if a stale google_connections
  // row would otherwise shortcut them to /home.
  if (!errorCode) {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.access_token && (await isGoogleConnected(session.access_token))) {
      // The last hop of a return-to-where-you-were-going sign-in. Validated
      // again here — the parameter is now visible in the URL bar, so this is
      // the point someone would try to bend it into an off-site redirect.
      redirect(returnTo ?? "/home");
    }
  }

  return (
    <ConnectPageClient
      initialError={resolveOauthErrorMessage(errorCode ?? null)}
      returnTo={returnTo}
    />
  );
}
