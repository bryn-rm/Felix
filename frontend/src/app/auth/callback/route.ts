import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";

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

  const response = NextResponse.redirect(new URL("/connect", origin));

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
