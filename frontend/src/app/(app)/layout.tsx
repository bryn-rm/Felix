import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { AuthSync } from "@/components/auth/AuthSync";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
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
    redirect("/login");
  }

  const {
    data: { session },
  } = await supabase.auth.getSession();

  // Fail closed: only an explicit {connected: true} passes. Any non-OK response
  // or network error bounces to /connect so users never land on /home without
  // Gmail access. redirect() throws, so keep it outside the try/catch.
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "";
  let connected = false;
  if (session?.access_token) {
    try {
      const res = await fetch(`${apiBase}/auth/google/status`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const status = (await res.json()) as { connected?: boolean };
        connected = status.connected === true;
      }
    } catch {
      connected = false;
    }
  }
  if (!connected) {
    redirect("/connect");
  }

  const displayName =
    (user.user_metadata?.full_name as string | undefined) ??
    (user.user_metadata?.name as string | undefined) ??
    null;

  return (
    <AppShell userEmail={user.email ?? ""} displayName={displayName}>
      <AuthSync />
      {children}
    </AppShell>
  );
}
