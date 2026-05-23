# Felix — AI Email & Calendar Chief of Staff

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

## Behavioral guidelines

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Project structure

felix/
├── CLAUDE.md
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
        └── migrations/                # incremental SQL migrations + new tables (001–013)

## Database tables (infra/schema.sql, infra/migrations)
- Auth & connections: google_connections, oauth_nonces
- User config: settings
- Email: emails, sent_emails, drafts, follow_ups, smart_templates
- Calendar: meetings, meeting_preps, pending_calendar_proposals
- Contacts & commitments: contacts, commitments
- Briefings & voice: briefings, voice_sessions
- Memory: user_memory, memory_episodes, memory_operations, session_summaries
- AI ops: ai_calls, ai_feedback, eval_runs
- Admin: admin_audit

## Testing
- Backend: `cd backend && pytest` (pytest + pytest-asyncio + freezegun)
- Frontend: `cd frontend && npm test` (Jest + React Testing Library)
