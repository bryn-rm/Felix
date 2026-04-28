import { redirect } from 'next/navigation'
import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'

export default async function RootPage() {
  const cookieStore = cookies()
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return cookieStore.getAll() },
        setAll(cookiesToSet: { name: string; value: string; options?: Record<string, unknown> }[]) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options)
          )
        },
      },
    }
  )

  // getUser() validates the JWT against the Supabase Auth server, unlike
  // getSession() which trusts whatever the cookie says. Worth the extra
  // round-trip on the root redirect to avoid honoring forged session cookies.
  const { data: { user } } = await supabase.auth.getUser()

  if (user) {
    redirect('/home')
  } else {
    redirect('/login')
  }
}
