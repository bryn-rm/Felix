# Felix

An AI email and calendar chief of staff. Felix connects to your Gmail and Google Calendar, triages your inbox, drafts replies in your voice, tracks follow-ups, and gives you a spoken morning briefing. Invite-only, fully self-hosted, every user's data is completely siloed.

---

## What it does

- **Inbox triage** — every incoming email is classified (action required / FYI / newsletter / VIP / etc.), assigned urgency and sentiment, and labelled in Gmail automatically
- **Draft replies** — Claude analyses your sent email history to learn your writing style, then pre-writes replies ready for your one-click approval
- **Follow-up tracking** — outbound emails that need a reply are monitored; Felix drafts and alerts you when they go cold
- **Voice interface** — speak to Felix to read emails, reply, schedule meetings, or get a summary (Phase 3)
- **Morning briefing** — a spoken daily briefing covering priority emails, today's calendar, and overdue follow-ups (Phase 4)
- **Relationship intelligence** — a living profile per contact: interaction history, open commitments, sentiment trends (Phase 6)

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14, TypeScript, Tailwind, shadcn/ui |
| Backend | FastAPI (Python), Cloud Run (GCP) |
| Auth | Supabase Auth with Google OAuth |
| Database | Supabase PostgreSQL with Row Level Security |
| AI | Claude Sonnet 4.6 (drafts/analysis) · Claude Haiku (triage/routing) |
| Voice | Google Cloud Speech-to-Text V2 · ElevenLabs Turbo v2.5 |
| Google APIs | Gmail API · Google Calendar API |
| Scheduler | APScheduler (inbox sync every 2 min, briefings, nightly refresh) |

---

## Project structure

```
felix/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry point
│   │   ├── config.py            # Pydantic settings (all env vars)
│   │   ├── db.py                # asyncpg helper (query/insert/upsert/update)
│   │   ├── api/                 # Route handlers (one file per domain)
│   │   ├── middleware/
│   │   │   └── auth.py          # JWT validation + Google credential loading
│   │   ├── services/            # Gmail, Calendar, AI, Voice, Style, Relationship
│   │   ├── jobs/                # APScheduler background tasks
│   │   ├── models/              # Pydantic models
│   │   └── prompts/             # All Claude system prompts
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                    # Next.js app (Phase 1+)
├── infra/
│   └── schema.sql               # Supabase schema — all tables + RLS policies
├── .env.example
├── CLAUDE.MD                    # Architecture rules and build phases
└── FELIX_BUILD_PLAN.md          # Full technical specification
```

---

## Setup

### Prerequisites

- Python 3.12+
- A [Supabase](https://supabase.com) project
- A GCP project with Gmail API, Calendar API, and Speech-to-Text API enabled
- An Anthropic API key
- An ElevenLabs API key

### 1. GCP / Google OAuth

In the [GCP Console](https://console.cloud.google.com):

```bash
# Enable required APIs
gcloud services enable \
  gmail.googleapis.com \
  calendar-json.googleapis.com \
  speech.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com
```

Go to **APIs & Services → OAuth consent screen**:
- User type: External
- Publishing status: **Testing** (keep it here permanently — you don't need to publish)
- Scopes: add Gmail + Calendar scopes listed in `backend/app/api/auth.py`
- **Test Users**: add every Google email that needs access — anyone not on this list is blocked

Go to **Credentials → Create OAuth Client ID → Web application**, add redirect URIs:
- `http://localhost:8000/auth/google/callback` (local)
- `https://your-cloud-run-url/auth/google/callback` (production)

### 2. Supabase

- Create a project at [supabase.com](https://supabase.com)
- Go to **Authentication → Providers → Google** and paste your Google OAuth client ID and secret
- Go to **SQL Editor** and run the contents of `infra/schema.sql`
- Go to **Settings → Database → Connection string** and copy the Session mode pooler URL (port 5432) — this is your `DATABASE_URL`

### 3. Environment variables

```bash
cp .env.example backend/.env
```

Fill in `backend/.env` — every variable is documented in `.env.example`. Key ones:

| Variable | Where to get it |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | GCP Console → Credentials |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/google/callback` for local |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Supabase → Settings → API |
| `DATABASE_URL` | Supabase → Settings → Database → Connection string (Session mode) |
| `TOKEN_ENCRYPTION_KEY` | `openssl rand -hex 32` |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `ELEVENLABS_API_KEY` / `FELIX_VOICE_ID` | [elevenlabs.io](https://elevenlabs.io) |

### 4. Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API is at `http://localhost:8000` — check `http://localhost:8000/docs` for the interactive spec.

---

## Adding a user

Felix is invite-only. To grant someone access:

1. GCP Console → APIs & Services → OAuth consent screen → Test Users → **Add Users**
2. Enter their Google email address → Save
3. Send them the app URL
4. They sign in with Google → connect Gmail & Calendar → Felix starts syncing

To revoke access: remove from Test Users and delete their row from `auth.users` in Supabase.

---

## Architecture rules

These apply everywhere in the codebase, no exceptions:

- **Every FastAPI route** uses `Depends(get_current_user)` from `middleware/auth.py`
- **Every database table** has a `user_id` column and an RLS policy
- **Every database write** includes `user_id` — RLS is a safety net, not a substitute
- **Background jobs** call `get_active_users()` and iterate — never hardcoded to one user
- **Google credentials** are loaded per-request via `get_google_credentials(user_id)` — never stored in env vars
- **All user config** (timezone, briefing time, style profile, VIPs) lives in the `settings` table

---

## Build phases

| Phase | Status | Scope |
|---|---|---|
| 1 — Auth + Google Connection | ✅ Complete | Supabase JWT, Google OAuth, encrypted token storage, onboarding |
| 2 — Inbox Triage + Drafts | 🔲 Next | Gmail sync, Claude triage, style profiling, draft generation |
| 3 — Voice Layer | 🔲 | WebSocket STT/TTS pipeline, voice commands |
| 4 — Calendar + Briefing | 🔲 | Calendar API, morning briefing generation + audio |
| 5 — Follow-up Engine | 🔲 | Outbound email tracking, auto-draft follow-ups |
| 6 — Relationship Intelligence | 🔲 | Contact profiles, sentiment trends, relationship alerts |
| 7 — Polish | 🔲 | Templates, digest mode, writing style evolution |

---

## Deployment

```bash
# Deploy backend to Cloud Run
gcloud run deploy felix-backend \
  --source ./backend \
  --region europe-west2 \
  --allow-unauthenticated \
  --min-instances 1 \
  --set-env-vars FRONTEND_URL=https://your-frontend.vercel.app \
  --set-secrets \
    ANTHROPIC_API_KEY=anthropic-key:latest,\
    ELEVENLABS_API_KEY=elevenlabs-key:latest,\
    GOOGLE_CLIENT_SECRET=google-client-secret:latest,\
    TOKEN_ENCRYPTION_KEY=token-encryption-key:latest
```

Frontend deploys to Vercel — set `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`, and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in the Vercel dashboard.
