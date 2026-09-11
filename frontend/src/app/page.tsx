import { redirect } from 'next/navigation'
import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import type { Metadata, Viewport } from 'next'
import PublicHomepage from '@/components/homepage/PublicHomepage'

const title = 'Felix — The working memory of your work'
const description = 'Felix carries context across email, meetings, commitments, and Projects. Remember what was discussed, decided, changed, and promised — and bring it into the next conversation.'
// Prefer the canonical company domain; Vercel supplies a production domain by
// default. Local development must not publish an invented canonical URL.
const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || (
  process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : undefined
)
const metadataBase = siteUrl ? new URL(siteUrl) : undefined
const images = metadataBase ? [{
  url: new URL('/icon-512.png', metadataBase).href,
  width: 512,
  height: 512,
  alt: 'Felix',
}] : undefined

export const metadata: Metadata = {
  metadataBase,
  title,
  description,
  applicationName: 'Felix',
  category: 'productivity',
  alternates: metadataBase ? { canonical: '/' } : undefined,
  openGraph: {
    type: 'website',
    siteName: 'Felix',
    title,
    description,
    url: metadataBase,
    images,
  },
  twitter: {
    card: 'summary',
    title,
    description,
    images,
  },
}

export const viewport: Viewport = {
  themeColor: '#f4f1ea',
  colorScheme: 'light',
}

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
  }

  return <PublicHomepage requestAccessUrl={process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL} />
}
