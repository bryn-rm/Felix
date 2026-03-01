# Felix — AI Email & Calendar Chief of Staff

Full detailed plan is in FELIX_BUILD_PLAN.md — read that first before doing anything.

## Stack
- Frontend: Next.js 14, TypeScript, Tailwind, shadcn/ui
- Backend: FastAPI (Python), Cloud Run (GCP)
- Auth: Supabase Auth with Google OAuth provider
- Database: Supabase PostgreSQL — RLS on every single table
- AI: Anthropic Claude API (claude-sonnet-4-6 for drafts/analysis, claude-haiku for triage/routing)
- Voice: Google Cloud Speech-to-Text V2 (STT) + ElevenLabs Turbo v2.5 (TTS)
- Google APIs: Gmail API + Google Calendar API via OAuth 2.0

## Non-negotiable architecture rules
- Every database table has a user_id column referencing auth.users + an RLS policy
- Google credentials are stored per-user in google_connections table, loaded via get_google_credentials(user_id)
- Every FastAPI route uses Depends(get_current_user) — no exceptions
- Background jobs iterate over get_active_users() — never hardcoded to one user
- All settings (timezone, briefing time, style profile, VIP contacts) are per-user rows in settings table

## Project structure
felix/
├── CLAUDE.md
├── FELIX_BUILD_PLAN.md
├── backend/              # FastAPI, Python
│   └── app/
│       ├── api/          # route handlers
│       ├── services/     # gmail, calendar, ai, voice
│       ├── jobs/         # APScheduler background tasks
│       ├── models/       # Pydantic models
│       ├── middleware/   # auth JWT validation
│       └── prompts/      # all Claude system prompts
└── frontend/             # Next.js 14, TypeScript

## Build phases — do these in order
Phase 1: Auth + Google Connection
Phase 2: Inbox Triage + Draft Replies  
Phase 3: Voice Layer
Phase 4: Calendar Integration
Phase 5: Follow-up Engine
Phase 6: Relationship Intelligence
Phase 7: Extra Features + Polish

## Start each session by reading both this file and FELIX_BUILD_PLAN.md
