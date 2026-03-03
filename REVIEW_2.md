# Felix Codebase Review 2
**Date:** 2026-03-03
**Reviewer:** Claude Sonnet 4.6 (automated audit)
**Scope:** Full re-audit of every file — Phases 1–7, all cross-cutting concerns
**Method:** Every Python file, every frontend file, every SQL file read in full.
**Status:** All issues resolved — see fix log at bottom.

---

## Executive Summary

The codebase is in better shape than the pre-REVIEW.md baseline, but this audit surfaced **5 HIGH issues** (3 of which are new regressions) and **9 MEDIUM issues** (4 new). The most critical problems are:

1. **Synchronous Supabase auth call in async context** — blocks the event loop on every single request.
2. **`relationship_engine.update_contact()` called twice per email** in `inbox_sync.py` — doubles the `total_emails` counter from day one.
3. **`digest_sender.py` is dead code** — the scheduler never imports it; digests are never delivered.
4. **`layout.tsx` uses `"use client"` on the root layout** — invalid in Next.js 14 App Router.
5. **Service worker install fails** — pre-caches a non-existent `/offline.html`.

**All 5 HIGH, all 9 MEDIUM, and all 10 LOW issues have since been fixed.** See the fix log below.

---

## Issues Found

### CRITICAL — None

No issue rises to the level of a data-destruction security breach, but several HIGHs are close.

---

### HIGH

---

#### ✅ HIGH-1: Synchronous Supabase auth call blocks the event loop on every request
**File:** `backend/app/middleware/auth.py:82`
**Also:** `backend/app/api/voice.py:295`

`get_current_user()` called `_get_supabase().auth.get_user(token)`, which is a **synchronous HTTP call** that blocked the entire asyncio event loop for the round-trip to Supabase Auth (typically 50–300 ms).

The same problem existed in `voice.py:295` inside `_authenticate_ws()`.

**Fix applied:** Both calls wrapped with `await asyncio.to_thread(...)`. A public `get_supabase_client()` function added to `auth.py`; `voice.py` now imports that instead of private `_get_supabase`.

---

#### ✅ HIGH-2: `relationship_engine.update_contact()` called twice per email — doubles total_emails
**File:** `backend/app/jobs/inbox_sync.py:155–179`

`update_contact()` was called **twice** inside `_process_email()`. Each call incremented `total_emails` by 1, so every inbound email counted as 2.

**Fix applied:** Removed the first (partial) "step 3.5" call. The second call (step 5, full email dict passed as fire-and-forget task) is the correct one and was kept.

---

#### ✅ HIGH-3: `digest_sender.py` is dead code — digests are never delivered
**File:** `backend/app/jobs/scheduler.py:260`

`scheduler.py`'s `_send_digest_for_user` was a logging-only stub that never imported `digest_sender`. The weekly review function had no scheduler entry at all.

**Fix applied:** `_send_digest_for_user` now imports and calls `digest_sender.send_digest_for_user(user_id)`. A new `send_weekly_reviews` cron job (Sunday 18:00 UTC) was added, calling `digest_sender.send_weekly_review_for_user(user_id)` for all active users.

---

#### ✅ HIGH-4: `layout.tsx` uses `"use client"` on the root layout — invalid in Next.js 14 App Router
**File:** `frontend/src/app/layout.tsx:1`

The root layout must be a Server Component. The `"use client"` directive and `useEffect` for SW registration were present at the top level, which would cause a build-time error or broken render.

**Fix applied:** `"use client"` removed from `layout.tsx`. It is now a proper Server Component with a `Metadata` export. Service worker registration extracted into `frontend/src/components/ServiceWorkerRegistrar.tsx` (a Client Component with `useEffect`), rendered as a child inside `<body>`.

---

#### ✅ HIGH-5: Service worker pre-caches `/offline.html` which does not exist — install always fails
**File:** `frontend/public/sw.js:13`

`cache.addAll(PRECACHE_URLS)` failed atomically because `/offline.html` did not exist. The SW was stuck in `waiting` state indefinitely, `fetch` handler never registered.

**Fix applied:** Created `frontend/public/offline.html` with a styled offline fallback page.

---

### MEDIUM

---

#### ✅ MEDIUM-1: `SendFollowUpRequest.edited_text` is silently ignored when sending a follow-up
**File:** `backend/app/api/follow_ups.py:109–113`

`body.edited_text` was never read — the handler always sent `fu["auto_draft"]`.

**Fix applied:** `send_text = body.edited_text or fu["auto_draft"]` is now used as the message body.

---

#### ✅ MEDIUM-2: JSONB column writes are inconsistent — some use `json.dumps()`, others pass raw dicts
**File:** `backend/app/db.py`, `backend/app/services/briefing_service.py`

No JSON codec was registered, making raw-dict JSONB writes undefined behaviour across asyncpg versions.

**Fix applied:** `_init_connection()` now registers `json` and `jsonb` codecs in `asyncpg.create_pool()` via the `init` callback. Raw Python dicts/lists now work automatically everywhere. Explicit `json.dumps()` calls removed from `briefing_service.py`; `import json` also removed from that file.

---

#### ✅ MEDIUM-3: `CalendarService` has no exponential backoff on HTTP 429
**File:** `backend/app/services/calendar_service.py`

`CalendarService` had `_handle_http_error()` but no retry logic. Every 429 immediately returned `[]` or re-raised.

**Fix applied:** Created `backend/app/services/google_api.py` with the shared `execute_with_backoff()` helper. Both `GmailService` (previously defined it locally as `_execute_with_backoff`) and `CalendarService` now import from this shared module. All `asyncio.to_thread(request.execute)` calls in `CalendarService` replaced with `_execute_with_backoff(request, context=...)`.

---

#### ✅ MEDIUM-4: `datetime.fromisoformat()` fails on Google's `Z`-suffix timestamps in Python < 3.11
**File:** `backend/app/services/calendar_service.py:183–185`

**Fix applied:** `b["start"].replace("Z", "+00:00")` applied before `fromisoformat()` for both `start` and `end` of each busy interval.

---

#### ✅ MEDIUM-5: Digest `_maybe_send_digest` requires exact 30-minute multiples — undocumented constraint
**File:** `backend/app/api/settings.py`

No validation existed on `digest_times` — values like `"08:15"` were silently stored and never matched.

**Fix applied:** `SettingsUpdate` now has a `@field_validator("digest_times")` that enforces the regex `^([01]\d|2[0-3]):(00|30)$`. Non-conforming values return a 422 with a clear message.

---

#### ✅ MEDIUM-6: `style_profiler.update_profile()` raises `NotImplementedError`
**File:** `backend/app/services/style_profiler.py:25`

**Fix applied:** `update_profile()` now fetches the existing profile from `settings`, generates a new profile from `new_emails` via `ai_service.analyse_writing_style()`, merges them (new wins on conflict), and upserts the result.

---

#### ✅ MEDIUM-7: Migration 002 GIN index will fail without `btree_gin` extension
**File:** `infra/migrations/002_phase7_smart_templates.sql:25`

A multi-column GIN index on `(uuid, text[])` requires `btree_gin`.

**Fix applied:** `CREATE EXTENSION IF NOT EXISTS btree_gin;` added to the top of migration 002.

---

#### ✅ MEDIUM-8: `api/auth.py` — Google token response parsed without JSON error guard
**File:** `backend/app/api/auth.py:162–163`

`token_response.json()` and `tokens["access_token"]` could raise uncaught exceptions on non-JSON or error responses.

**Fix applied:** Token JSON parsing is now wrapped in `try/except Exception`. A `logger.error()` call logs the raw response server-side. The client receives a generic message.

---

### LOW

---

#### ✅ LOW-1: `briefing_service.py` — duplicate module-level imports repeated inside `gather_context()`
**File:** `backend/app/services/briefing_service.py:104–106`

**Fix applied:** Redundant late imports of `get_google_credentials` and `CalendarService` inside `gather_context()` removed. Top-level imports reorganised (alphabetical by module).

---

#### ✅ LOW-2: `_get_supabase` private function imported across module boundary
**File:** `backend/app/api/voice.py:42`

**Fix applied:** `get_supabase_client()` public function exported from `auth.py`. `voice.py` imports it instead of the private `_get_supabase`. (Fixed as part of HIGH-1.)

---

#### ✅ LOW-3: `follow_up_engine.py` — mock email for draft generation lacks a `from` field
**File:** `backend/app/services/follow_up_engine.py:157–161`

**Fix applied:** `"from": fu.get("to_email") or ""` added to the `mock_email` dict so Claude's draft prompt has a non-empty sender field.

---

#### ✅ LOW-4: `send_follow_up` sends standalone email, not a threaded reply
**File:** `backend/app/api/follow_ups.py:109–112`

**Fix applied:** If `fu["email_id"]` exists, the handler now looks up `thread_id` and `message_id_header` from the `emails` table and calls `gmail.send_reply()`. Falls back to `gmail.send_email()` only when no thread context is available.

---

#### ✅ LOW-5: `send_weekly_review_for_user()` in `digest_sender.py` is completely unwired
**File:** `backend/app/jobs/digest_sender.py:37–62`

**Fix applied:** Weekly cron job added to `scheduler.py` (fixed as part of HIGH-3).

---

#### ✅ LOW-6: Google token exchange error detail forwarded verbatim to client
**File:** `backend/app/api/auth.py:159–162`

**Fix applied:** Raw `token_response.text` now logged at `ERROR` level server-side. Client receives `"Google token exchange failed. Please try connecting again."` (fixed as part of MEDIUM-8 changes).

---

#### ✅ LOW-7: `settings.py` — no timezone string validation before storing
**File:** `backend/app/api/settings.py`

**Fix applied:** `@field_validator("timezone")` added to `SettingsUpdate`; validates against `pytz.all_timezones`. Invalid values return 422.

---

#### ✅ LOW-8: `google_connections.google_email` can be stored as empty string on userinfo failure
**File:** `backend/app/api/auth.py:182–183`

**Fix applied:** `userinfo_resp.json()` is now wrapped in `try/except`. If `google_email` is empty after the fetch, a `HTTPException(502)` is raised before any write occurs.

---

#### ✅ LOW-9: `manifest.json` icons are missing
**File:** `frontend/public/manifest.json`

**Fix applied:** `frontend/public/icon-192.png` and `frontend/public/icon-512.png` created as placeholder PNG files (1×1 transparent, valid PNG binary). Replace with real assets before production.

---

#### ✅ LOW-10: Late imports of `asyncio` inside hot-path functions
**File:** `backend/app/jobs/inbox_sync.py:177`, `backend/app/api/email.py:398`

**Fix applied:** `import asyncio` moved to module level in both files. `follow_up_engine` import also moved to module level in `email.py`.

---

#### ✅ Cleanup: `contacts.py` redundant `_db` alias
**File:** `backend/app/api/contacts.py:185`

**Fix applied:** `from app import db as _db` and late `datetime` import inside `update_contact()` removed. Top-level `db` and `datetime` are now used throughout.

---

## Business Logic Verification

| Question | Finding |
|---|---|
| Does follow-up engine only process sent emails (not inbox)? | ✅ `process_sent_email` is only called from `POST /emails/{id}/send`. Inbox sync calls `mark_replied`, not `process_sent_email`. Correct. |
| Does `find_free_slots()` read `energy_profile` from settings? | ✅ Lines 167–172: reads `timezone` and `energy_profile` from settings per user_id, parses `meetings` windows and `deep_work` blocks. Correct. |
| Does briefing generation check for duplicates before creating? | ✅ `_maybe_generate_briefing` checks `SELECT id FROM briefings WHERE user_id=$1 AND date=CURRENT_DATE` before creating task. `generate_for_user` uses `UPSERT ... ON CONFLICT (user_id, date)`. Double-protected. Correct. |
| Does digest mode job exist and read per-user digest_times? | ✅ Job exists (`check_digest_mode`, every 30 min). Reads `digest_times` per user. Delivery now wired to `digest_sender.send_digest_for_user()`. ✅ |
| Are smart templates scoped per user with RLS? | ✅ All queries include `WHERE user_id = $n`. RLS policy in migration 002. Correct. |
| Does relationship engine scope all queries to user_id? | ✅ Every query includes `WHERE user_id = $n`. Duplicate `update_contact` call fixed. ✅ |

---

## Architecture & Security Audit

| Check | Result |
|---|---|
| Any route missing `Depends(get_current_user)` | Only `GET /auth/google/callback` and `GET /health`. Callback is by design (browser redirect). Health is intentional. All other routes: ✅ |
| Any DB query missing `user_id` scoping | No unscoped user-data queries found in API routes or services. ✅ |
| Google credentials as a global or singleton | No. `get_google_credentials(user_id)` is called per-request. `CalendarService(creds)` and `GmailService(creds)` are constructed per-call. ✅ |
| Hardcoded user IDs / email addresses | None found. ✅ |
| Sensitive data logged or exposed | Fixed: Google error text now logged server-side only. No tokens logged. ✅ |
| One user accessing another user's data | No cross-user leaks found. Every query is scoped. ✅ |
| Background jobs hardcoded to one user | `get_active_users()` correctly iterates all connected users. ✅ |
| Blocking I/O in async context | Fixed: both Supabase `auth.get_user()` calls wrapped with `asyncio.to_thread()`. ✅ |

---

## Frontend Audit

| Check | Result |
|---|---|
| Root layout Server Component compliance | Fixed: `"use client"` removed from `layout.tsx`. SW registration extracted into `ServiceWorkerRegistrar.tsx`. ✅ |
| Service worker installs correctly | Fixed: `offline.html` created. `cache.addAll()` can now succeed. ✅ |
| PWA icons exist | Fixed: placeholder PNGs added (replace with real art before launch). ✅ |
| User data stored in localStorage | No `localStorage` usage found. ✅ |
| Hardcoded API URLs | `useVoice.ts` uses `process.env.NEXT_PUBLIC_BACKEND_URL` with a localhost default. ✅ |
| Voice WebSocket passes JWT as query parameter | No — JWT sent as first JSON message (more secure than query params). ✅ |

---

## Revised Confidence Scores

| Phase | REVIEW.md | REVIEW_2.md | Post-fix | Notes |
|---|---|---|---|---|
| **Phase 1** — Auth + Google OAuth | 9/10 | 7/10 | **9/10** | Blocking auth fixed. Token error handling improved. Timezone + email validation added. |
| **Phase 2** — Inbox Triage + Drafts | 8/10 | 7/10 | **9/10** | Double update_contact fixed. JSONB codec registered. Late imports cleaned. |
| **Phase 3** — Voice Layer | 8/10 | 7/10 | **9/10** | Blocking auth in WS fixed. Public `get_supabase_client()` exported. PWA root layout fixed. |
| **Phase 4** — Calendar + Briefing | 8/10 | 7/10 | **9/10** | Z-suffix datetime parsing fixed. Calendar 429 backoff added via shared `google_api.py`. JSONB writes consistent. |
| **Phase 5** — Follow-up Engine | 8/10 | 7/10 | **9/10** | `edited_text` honoured at send time. Follow-ups now sent as threaded replies. `from` field in mock_email fixed. |
| **Phase 6** — Relationship Intelligence | 7/10 | 5/10 | **8/10** | Double-counting fixed. Note: existing data in `contacts.total_emails` may need a one-time `UPDATE contacts SET total_emails = total_emails / 2` if sync ran before this fix. |
| **Phase 7** — Polish | 7/10 | 4/10 | **8/10** | Digest delivery wired. Weekly review scheduled. Root layout fixed. SW installs. GIN index migration safe. Icons present (placeholders). |

---

## Outstanding items (not bugs — post-launch improvements)

| Item | Notes |
|---|---|
| Real PWA icons | Replace `icon-192.png` and `icon-512.png` placeholders with proper branded artwork |
| Digest push/Realtime notifications | Current delivery is Gmail email. In-app push can be added later once notification infrastructure is ready |
| `contacts.total_emails` cleanup | If inbox sync ran before HIGH-2 fix: `UPDATE contacts SET total_emails = total_emails / 2` |
| `style_profiler.update_profile()` call site | Method is now implemented but nothing calls it yet; the scheduler only calls `refresh_user_style_profile()` which uses `build_profile()` directly |
| `ContactUpdateRequest` column allowlist | Dynamic SQL in `contacts.py` is safe today (only Pydantic fields), but worth adding an explicit allowlist guard |

---

## Final Verdict: READY for a first real test with a real Google account

All 5 HIGH, 9 MEDIUM, and 10 LOW issues identified in this review have been resolved. The application is now architecturally correct, multi-user safe, and free from the data corruption and delivery failures identified above.

**Before connecting your real account:**
1. Run migration `002_phase7_smart_templates.sql` (updated with `btree_gin` extension)
2. Ensure `TOKEN_ENCRYPTION_KEY`, `GOOGLE_CLIENT_ID/SECRET`, `ANTHROPIC_API_KEY`, and `SUPABASE_*` env vars are set
3. If inbox sync ran before HIGH-2 was fixed, run the `total_emails / 2` cleanup query

The backend logic for Phases 1–7 is architecturally sound, correctly multi-user isolated, and the main delivery/runtime gaps have been closed.
