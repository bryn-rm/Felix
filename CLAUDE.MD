# Felix — AI Email & Calendar Chief of Staff

Full detailed plan is in FELIX_BUILD_PLAN.md — read that first before doing anything.

## Stack
- Frontend: Next.js 14.2 (App Router), React 18, TypeScript, Tailwind CSS 3.4
- Backend: FastAPI (Python), Cloud Run (GCP), Dockerfile at backend/Dockerfile
- Auth: Supabase Auth with Google OAuth provider
- Database: Supabase PostgreSQL — RLS on every single table (schema in infra/schema.sql, migrations in infra/migrations/)
- AI: Anthropic Claude API (claude-sonnet-4-6 for drafts/analysis, claude-haiku for triage/routing)
- Voice: Google Cloud Speech-to-Text V2 (STT) + ElevenLabs Turbo v2.5 (TTS)
- Google APIs: Gmail API + Google Calendar API via OAuth 2.0
- Key frontend libs: @supabase/ssr, @supabase/supabase-js, swr (data fetching), recharts, lucide-react, dompurify
- Key backend libs: anthropic, google-api-python-client, google-cloud-speech, elevenlabs, apscheduler, asyncpg, httpx, cryptography (Fernet encryption)

## Non-negotiable architecture rules
- Every database table has a user_id column referencing auth.users + an RLS policy
- Google credentials are stored per-user in google_connections table, loaded via get_google_credentials(user_id)
- Every FastAPI route uses Depends(get_current_user) — no exceptions
- Background jobs iterate over get_active_users() — never hardcoded to one user
- All settings (timezone, briefing time, style profile, VIP contacts) are per-user rows in settings table

## Project structure
```
felix/
├── CLAUDE.md
├── FELIX_BUILD_PLAN.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt      # pytest, pytest-asyncio, freezegun
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_auth.py
│   │   ├── test_email_triage.py
│   │   ├── test_settings.py
│   │   └── test_timezone_utils.py
│   └── app/
│       ├── api/                   # route handlers
│       │   ├── auth.py, email.py, calendar.py, contacts.py
│       │   ├── briefing.py, follow_ups.py, voice.py
│       │   ├── settings.py, templates.py, polish.py, eval.py
│       ├── services/              # business logic
│       │   ├── ai_service.py, gmail_service.py, calendar_service.py
│       │   ├── voice_service.py, voice_router.py
│       │   ├── style_profiler.py, polish_service.py
│       │   ├── sentiment_analyser.py, relationship_engine.py
│       │   ├── follow_up_engine.py, briefing_service.py
│       │   ├── google_api.py, timezone_utils.py
│       ├── jobs/                  # APScheduler background tasks
│       │   ├── scheduler.py       # job registration
│       │   ├── inbox_sync.py, briefing_generator.py
│       │   ├── follow_up_checker.py, digest_sender.py
│       │   └── relationship_updater.py
│       ├── models/                # Pydantic models
│       │   ├── user.py, email.py, contact.py
│       │   ├── briefing.py, follow_up.py
│       ├── middleware/
│       │   └── auth.py            # JWT validation
│       └── prompts/               # Claude system prompts
│           ├── triage.py, draft.py, briefing.py
│           ├── sentiment.py, style_analysis.py
│           ├── follow_up_detection.py, meeting_notes.py
│           └── voice_intent.py
├── frontend/
│   ├── package.json
│   ├── next.config.mjs, tailwind.config.ts, tsconfig.json
│   ├── public/                    # PWA: manifest.json, sw.js, offline.html
│   └── src/
│       ├── app/
│       │   ├── (app)/             # authenticated routes (dashboard, inbox, etc.)
│       │   ├── (auth)/            # login/signup routes
│       │   ├── api/               # Next.js API routes
│       │   └── auth/              # auth callback handlers
│       ├── components/
│       │   ├── felix/VoiceOrb.tsx # voice interface orb
│       │   ├── calendar/, contacts/, email/
│       │   ├── follow-ups/, inbox/, templates/
│       │   └── layout/            # shared layout components
│       ├── hooks/                 # useEmails, useCalendar, useDraft, useFollowUps, useVoice, useUnreadCounts
│       └── lib/                   # api.ts, supabase.ts, supabase-server.ts, types.ts
└── infra/
    ├── schema.sql                 # full DB schema (10 tables, all with RLS)
    └── migrations/                # incremental SQL migrations (001–005)
```

## Database tables (infra/schema.sql)
google_connections, settings, emails, drafts, follow_ups, contacts, meetings, briefings, voice_sessions

## Testing
- Backend: `cd backend && pytest` (pytest + pytest-asyncio + freezegun)
- Frontend: `cd frontend && npm test` (Jest + React Testing Library)

## Build phases — do these in order
Phase 1: Auth + Google Connection
Phase 2: Inbox Triage + Draft Replies
Phase 3: Voice Layer
Phase 4: Calendar Integration
Phase 5: Follow-up Engine
Phase 6: Relationship Intelligence
Phase 7: Extra Features + Polish

## Start each session by reading both this file and FELIX_BUILD_PLAN.md
