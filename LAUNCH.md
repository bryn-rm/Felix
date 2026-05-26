# Felix — Local Launch Guide

A foolproof guide to get Felix running on your machine.

---

## Prerequisites

Before you start, make sure you have:

| Tool | Version | Check |
|------|---------|-------|
| Node.js | 18+ | `node -v` |
| Python | 3.12+ | `python3 --version` |
| npm | 9+ | `npm -v` |

You'll also need accounts/keys for:
- **Supabase** — free tier works (supabase.com)
- **Google Cloud** — OAuth credentials + project
- **Anthropic** — API key (console.anthropic.com)
- **ElevenLabs** — API key + voice ID (required by config, even if you don't use voice briefings)

---

## Step 1 — Set up environment variables

### Backend `.env`

```bash
cp .env.example backend/.env
```

Open `backend/.env` and fill in every value. Here's what each one needs:

```bash
# ── Google OAuth ──────────────────────────────────────────────
# From GCP Console → APIs & Services → Credentials → OAuth 2.0 Client ID
GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback   # leave as-is for local dev

# ── GCP ───────────────────────────────────────────────────────
# Your GCP project ID (find it in GCP Console header dropdown)
GCP_PROJECT_ID=my-project-123456
GCP_REGION=europe-west2

# ── Anthropic ─────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-xxxx
ANTHROPIC_MODEL_SMART=claude-sonnet-4-6          # leave as-is
ANTHROPIC_MODEL_FAST=claude-haiku-4-5-20251001   # leave as-is

# ── ElevenLabs ────────────────────────────────────────────────
# Get from elevenlabs.io → Profile → API Key
# Voice ID: pick any voice from elevenlabs.io/voice-library, copy its ID from the URL
ELEVENLABS_API_KEY=xxxx
FELIX_VOICE_ID=xxxx
# Optional approved voices shown in Settings → Voice.
# Add voices here locally, or set the same env var in Railway/production.
FELIX_VOICE_CATALOG='[{"id":"21m00Tcm4TlvDq8ikWAM","label":"Rachel"}]'

# ── Supabase ──────────────────────────────────────────────────
# From Supabase dashboard → Settings → API
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJh...   # use the "service_role" key (NOT anon key)

# From Supabase dashboard → Settings → Database → Connection string → Session mode
# Make sure to use port 5432 (session mode), not 6543 (transaction mode)
DATABASE_URL=postgresql://postgres.xxxx:password@aws-0-eu-west-2.pooler.supabase.com:5432/postgres

# ── Security ──────────────────────────────────────────────────
# Generate fresh values with: openssl rand -hex 32
TOKEN_ENCRYPTION_KEY=<run: openssl rand -hex 32>
BACKEND_SECRET_KEY=<run: openssl rand -hex 32>

# ── App ───────────────────────────────────────────────────────
FRONTEND_URL=http://localhost:3000   # for Codespaces, set this to your current frontend https://<name>-3000.app.github.dev URL
ADMIN_EMAIL=                         # optional — your email to access /admin routes
```

### Frontend `.env.local`

Create the file:

```bash
cat > frontend/.env.local << 'EOF'
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJh...
# Optional override for OAuth redirects. If omitted, frontend uses window.location.origin.
# In Codespaces you can set this to your current frontend URL: https://<name>-3000.app.github.dev
NEXT_PUBLIC_APP_URL=
EOF
```

- `NEXT_PUBLIC_SUPABASE_URL` — same URL as backend
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` — use the **anon/public** key (NOT service role) from Supabase → Settings → API

---

## Step 2 — Set up Supabase

### 2a. Create a project

1. Go to [supabase.com](https://supabase.com) → New project
2. Choose a region close to you
3. Save your database password somewhere safe

### 2b. Run the schema

1. In Supabase dashboard → **SQL Editor** → **New query**
2. Open `/workspaces/Felix/infra/schema.sql`, paste the entire contents, click **Run**
3. You should see "Success. No rows returned"

### 2c. Run migrations (in order)

Repeat the paste-and-run process for each file:

```
infra/migrations/001_phase2_email_fields.sql
infra/migrations/002_phase7_smart_templates.sql
infra/migrations/003_oauth_nonces.sql
infra/migrations/004_eval_infrastructure.sql
infra/migrations/005_schema_hardening.sql
```

Run them **in order**, one at a time.

### 2d. Enable Google OAuth

1. Supabase dashboard → **Authentication** → **Providers** → **Google**
2. Toggle **Enable**
3. Paste your `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` from Step 1
4. In **Authentication → URL Configuration**, add your app callback URL exactly:
   - Local: `http://localhost:3000/auth/callback`
   - Production: `https://your-frontend-domain/auth/callback`
5. Copy the **Callback URL** shown — you'll need it in the next step

---

## Step 3 — Set up Google OAuth (GCP Console)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or select an existing one) — note the Project ID for `GCP_PROJECT_ID`
3. **APIs & Services → Library** — enable these APIs:
   - Gmail API
   - Google Calendar API
   - Cloud Speech-to-Text API
4. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Web application**
   - Authorized redirect URIs — add **both**:
     - `http://localhost:8000/auth/google/callback`
     - The Supabase callback URL from Step 2d (looks like `https://xxxx.supabase.co/auth/v1/callback`)
5. Download/copy the Client ID and Client Secret → paste into `backend/.env`

---

## Step 4 — Launch the backend

```bash
cd /workspaces/Felix/backend

# Create virtual environment (first time only)
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install dependencies (first time only, or after pulling new code)
pip install -r requirements.txt

# Start the server
uvicorn app.main:app --reload --port 8000
```

**Expected output:**

```
INFO:     Felix backend starting up
INFO:     Database connection verified
INFO:     APScheduler started
INFO:     Uvicorn running on http://0.0.0.0:8000
```

If you see "Database connection verified" and "APScheduler started" — the backend is healthy.

**Quick health check:**

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

**API explorer** (optional): open `http://localhost:8000/docs` in your browser to browse all endpoints.

---

## Step 5 — Launch the frontend

Open a **new terminal tab** (keep the backend running):

```bash
cd /workspaces/Felix/frontend

# Install dependencies (first time only)
npm install

# Start dev server
npm run dev
```

**Expected output:**

```
▲ Next.js 14.x.x
- Local: http://localhost:3000
- Ready in Xs
```

Open `http://localhost:3000` in your browser.

---

## Step 6 — First login

1. You'll land on the login page
2. Click **Sign in with Google**
3. Choose your Google account
4. Grant the requested permissions (Gmail + Calendar access)
5. You'll be redirected to the onboarding flow → then the dashboard

> If you get a redirect mismatch error from Google, double-check that `http://localhost:8000/auth/google/callback` is listed in your GCP OAuth credentials (Step 3, redirect URIs).

---

## Smoke test checklist

Once logged in, verify each area:

- [ ] **Dashboard** loads without errors (no red banners in console)
- [ ] **Inbox** tab — emails appear after the first sync (may take ~30s)
- [ ] **Calendar** tab — meetings show for the current week
- [ ] **Follow-ups** tab — loads without error
- [ ] **Contacts** tab — loads without error
- [ ] **Templates** tab — loads without error
- [ ] **Briefing** tab — can generate a briefing
- [ ] **Settings** tab — can view and save preferences
- [ ] Backend logs — no `500` errors in the uvicorn terminal

---

## Common errors

### `pydantic_settings.env_settings.EnvSettingsError` on backend start
A required env var is missing from `backend/.env`. Read the error — it names the missing variable. Check Step 1.

### `DATABASE_URL` connection failed / asyncpg error
- Make sure you're using the **Session mode** pooler URL (port **5432**), not the transaction mode URL (port 6543)
- Find it in Supabase → Settings → Database → **Connection string** → select **Session** tab

### CORS error in browser console
`FRONTEND_URL` in `backend/.env` doesn't match the URL your browser is using. For local dev it should be `http://localhost:3000` exactly (no trailing slash).

### Google OAuth — `redirect_uri_mismatch`
The redirect URI in your GCP credentials doesn't exactly match `GOOGLE_REDIRECT_URI` in `.env`. Make sure both are `http://localhost:8000/auth/google/callback`.

### `401 Unauthorized` on all API calls
The Supabase session isn't being passed to the backend. Check that `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in `frontend/.env.local` are correct and that you restarted the dev server after editing the file.

### Frontend shows blank page / hydration errors
Run `npm run build` from the `frontend/` directory to see TypeScript/build errors clearly, then fix and re-run `npm run dev`.

### ElevenLabs errors in backend logs
If you don't have an ElevenLabs account, the voice briefing audio generation will fail — but everything else will work. The config requires `ELEVENLABS_API_KEY` and `FELIX_VOICE_ID` to be non-empty, so put any placeholder string in `.env` to unblock startup, then add real values when you want voice features.

### Add voices to the Settings dropdown
Add approved ElevenLabs voices to `FELIX_VOICE_CATALOG` as JSON, then restart/redeploy the backend. For local development this lives in `backend/.env`; for production set the same variable in Railway. Each entry needs an ElevenLabs voice `id` and a user-facing `label`.

  Format:
  FELIX_VOICE_CATALOG='[{"id":"21m00Tcm4TlvDq8ikWAM","label":"Rachel"},
  {"id":"VOICE_ID","label":"Voice name"}]'

---

## Quick reference

| Service | URL | Notes |
|---------|-----|-------|
| Frontend | http://localhost:3000 | Next.js dev server |
| Backend API | http://localhost:8000 | FastAPI + Uvicorn |
| API docs | http://localhost:8000/docs | Swagger UI |
| Health check | http://localhost:8000/health | No auth required |

| Command | What it does |
|---------|-------------|
| `source .venv/bin/activate` | Activate Python venv (backend) |
| `uvicorn app.main:app --reload --port 8000` | Start backend with hot reload |
| `npm run dev` | Start frontend with hot reload |
| `npm run build` | Build frontend (reveals TS errors) |
| `openssl rand -hex 32` | Generate a secret key |
