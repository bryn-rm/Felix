import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";

import { RETURN_TO_COOKIE, safeReturnPath } from "@/lib/return-to";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");

  // Google/Supabase append an OAuth error here instead of a code — e.g. a user who
  // isn't on the OAuth test-users list, or who declined consent. Forward the real
  // reason so /login can show a tailored message. The full param set is logged so
  // the exact value Supabase passes through can be confirmed against a live denied
  // flow (the /login mapping matches defensively on "denied" regardless).
  const oauthError = searchParams.get("error") ?? searchParams.get("error_code");
  if (oauthError) {
    console.log(
      "[callback] oauth error params:",
      Object.fromEntries(searchParams.entries()),
    );
    return NextResponse.redirect(
      new URL(`/login?error=${encodeURIComponent(oauthError)}`, origin),
    );
  }

  if (!code) {
    console.log("[callback] no code in URL, redirecting to /login");
    return NextResponse.redirect(new URL("/login?error=missing_code", origin));
  }

  console.log("[callback] exchanging code for session...");

  // Where the user was headed before being sent to sign in. Re-validated here
  // rather than trusted: it arrives in a cookie this app wrote, but a cookie is
  // still client-side state. It rides on to /connect, which is the hop that
  // decides between it and /home once Gmail access is confirmed — this route
  // must not skip that check.
  const stored = request.cookies.get(RETURN_TO_COOKIE)?.value;
  // NextRequest.cookies has already decoded the wire value. Decoding it again
  // makes a legitimate trailing `%25` become a bare `%` and throws URIError,
  // wedging every callback until the browser cookie is manually cleared.
  const returnTo = safeReturnPath(stored);
  const destination = returnTo
    ? `/connect?next=${encodeURIComponent(returnTo)}`
    : "/connect";

  const response = NextResponse.redirect(new URL(destination, origin));
  response.cookies.delete(RETURN_TO_COOKIE);

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet: { name: string; value: string; options?: Record<string, unknown> }[]) {
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options as Parameters<typeof response.cookies.set>[2]),
          );
        },
      },
    },
  );

  const { error } = await supabase.auth.exchangeCodeForSession(code);

  if (error) {
    console.log("[callback] exchange failed:", error.message);
    return NextResponse.redirect(
      new URL(`/login?error=${encodeURIComponent(error.message)}`, origin),
    );
  }

  console.log("[callback] exchange succeeded, redirecting to /connect");
  return response;
}
