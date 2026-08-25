import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { PATHNAME_HEADER } from "@/lib/return-to";

export async function middleware(request: NextRequest) {
  // Stamp the path being rendered so the app layout can send an
  // unauthenticated visitor back to it after signing in — a Server Component
  // has no other way to know which URL it was asked for. Read fresh on every
  // call because @supabase/ssr mutates `request.cookies` (and therefore the
  // cookie header) between them, and `set`, never `append`, so a header a
  // client sent under this name cannot survive to be trusted downstream.
  const forwarded = () => {
    const headers = new Headers(request.headers);
    headers.set(
      PATHNAME_HEADER,
      `${request.nextUrl.pathname}${request.nextUrl.search}`,
    );
    return NextResponse.next({ request: { headers } });
  };

  let response = forwarded();

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet: { name: string; value: string; options?: Record<string, unknown> }[]) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          response = forwarded();
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options as Parameters<typeof response.cookies.set>[2]),
          );
        },
      },
    },
  );

  // Refresh session if expired — required for Server Components
  await supabase.auth.getUser();

  return response;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon\\.ico|manifest\\.json|icons|sw\\.js).*)",
  ],
};