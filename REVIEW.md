# Felix Codebase Review
**Date:** 2026-03-02
**Reviewer:** Claude Sonnet 4.6 (automated audit)
**Scope:** Full codebase — Phases 1–7 + cross-cutting concerns

---

## Issues Found and Fixed

### CROSS-CUTTING — `middleware/auth.py`

#### ✅ FIXED — Issue 1: `import os` unused (line 15)
**File:** `backend/app/middleware/auth.py:15`
**Problem:** `import os` was present but `os` is never referenced anywhere in the file. Dead code.
**Fix:** Replaced `import os` with `import asyncio` (needed for the fix below).

---

#### ✅ FIXED — Issue 2: `Credentials()` constructed without `expiry` — auto-refresh never fires (lines 116–125)
**File:** `backend/app/middleware/auth.py:116`
**Problem:** The `google.oauth2.credentials.Credentials` object was built without the `expiry=` parameter. The `Credentials.expired` property checks whether `expiry` is before `now()`. Without `expiry` set, `creds.expired` always returns `False`, so the auto-refresh block (`if creds.expired and creds.refresh_token`) was silently skipped every time. Access tokens were never refreshed programmatically — they would eventually expire and all Google API calls would start failing with HTTP 401.
**Fix:** Parse `token_expiry` from the DB row (handles both `datetime` objects from asyncpg and ISO string fallback) and pass `expiry=expiry` to the `Credentials()` constructor.

---

#### ✅ FIXED — Issue 3: `creds.refresh(Request())` blocks the event loop (line 125)
**File:** `backend/app/middleware/auth.py:125`
**Problem:** `google.auth.transport.requests.Request` is synchronous HTTP. Calling `creds.refresh(Request())` directly in an `async` function blocks the asyncio event loop, stalling all other concurrent requests for the duration of the HTTP round-trip (typically 200–800 ms).
**Fix:** Changed to `await asyncio.to_thread(creds.refresh, Request())`.

---

### CROSS-CUTTING — `jobs/inbox_sync.py`

#### ✅ FIXED — Issue 4: module-level `_label_cache` shared across all users (line 174)
**File:** `backend/app/jobs/inbox_sync.py:174`
**Problem:** `_label_cache: dict[str, str] = {}` was a module-level singleton. Each Gmail account uses its own internal label IDs for the same label names. If the scheduler synced user A first and cached `"Felix/Action Required" → "Label_1234"`, then when syncing user B it would reuse user A's label ID. Applying a foreign user's label ID in Gmail silently fails or — worse — modifies the wrong label.
**Fix:** Removed the module-level dict. A fresh `label_cache: dict[str, str] = {}` is now initialised per call to `sync_user_inbox()` and passed through `_process_email` → `_apply_gmail_labels` → `_get_or_create_labels` as an explicit parameter. Label IDs are now fully isolated per user and per sync run.

---

### CROSS-CUTTING — `services/gmail_service.py`

#### ✅ FIXED — Issue 5: missing HttpError 401/403/429 handling + exponential back-off
**File:** `backend/app/services/gmail_service.py` — multiple methods
**Problem:** Only inner per-message fetches had `except HttpError`. The outer `list()`, `threads().get()`, labels, and `_send_raw()` had no error handling. Additionally, HTTP 429 (rate limit) responses had no back-off — the next scheduler cycle would retry immediately and likely get 429 again.
**Fix:**
- Added `_handle_http_error(error, context)` utility logging structured warnings for 401/403/429.
- Wrapped every `asyncio.to_thread(request.execute)` with `try/except HttpError`.
- Added `_execute_with_backoff(request, context, max_retries=3)` with exponential wait `2^attempt` seconds (2s, 4s, 8s) on 429. Applied to list inbox, list sent, and get thread calls.

---

### CROSS-CUTTING — `services/ai_service.py`

#### ✅ FIXED — Issue 6: `generate_meeting_notes` missing `json.loads` guard (line 175)
**File:** `backend/app/services/ai_service.py:175`
**Problem:** `return json.loads(response.content[0].text)` had no `try/except`. If Claude returned a preamble or markdown wrapper, `JSONDecodeError` would propagate and crash the caller.
**Fix:** Added `try/except json.JSONDecodeError` returning a safe fallback dict with `summary`, `action_items`, `decisions`, `open_questions` keys.

---

#### ✅ FIXED — Issue 7: `parse_voice_intent` missing `json.loads` guard (line 205)
**File:** `backend/app/services/ai_service.py:205`
**Problem:** Same as above — bare `json.loads()` in the middle of the WebSocket voice session.
**Fix:** Added `try/except json.JSONDecodeError` returning `{"intent": "general_question", "raw_transcript": transcript}`.

---

### PHASE 4 — `jobs/scheduler.py`

#### ✅ FIXED — Issue 8: `check_morning_briefings` uses sequential `for` loop (lines 102–103)
**Fix:** Replaced with `asyncio.gather(*[...], return_exceptions=True)`. Consistent with all other scheduler jobs.

#### ✅ FIXED — Issue 9: `_generate_briefing_for_user` stub silently did nothing
**Fix:** Wired to `briefing_generator.generate_briefing_for_user(user_id)`.

#### ✅ FIXED — Issue 10: `_check_user_follow_ups` stub silently did nothing
**Fix:** Wired to `follow_up_checker.check_user_follow_ups(user_id)`.

#### ✅ FIXED — Issue 11: `_refresh_user_relationships` stub silently did nothing
**Fix:** Wired to `relationship_updater.refresh_user_relationships(user_id)`.

---

### PHASE 7 — PWA + Templates

#### ✅ FIXED — Issue 12: PWA manifest absent
**File:** `frontend/public/manifest.json` (created)

#### ✅ FIXED — Issue 13: Service worker absent
**File:** `frontend/public/sw.js` (created) — cache-first for static assets, network-only for API/WebSocket, network-first with offline fallback for navigation.

#### ✅ FIXED — Issue 14: Service worker never registered
**File:** `frontend/src/app/layout.tsx` (created)
**Fix:** Root layout calls `navigator.serviceWorker.register('/sw.js')` in `useEffect` and includes `<link rel="manifest">` in `<head>`.

#### ✅ FIXED — Issue 15: CSRF nonce not verified in OAuth callback
**File:** `backend/app/api/auth.py:google_callback`
**Problem:** `connect_google` stored a nonce and encoded state as `"<user_id>.<nonce>"`, but `google_callback` still read `user_id = state` — meaning it received `"uuid.nonce"` as the user_id, which would fail all subsequent DB queries.
**Fix:** Callback now parses `user_id, nonce = state.split(".", 1)`, queries `oauth_nonces`, validates the nonce matches and hasn't expired, deletes the row (one-time use), then proceeds with the token exchange.

#### ✅ FIXED — Issue 16: `oauth_nonces` table migration missing
**File:** `infra/migrations/003_oauth_nonces.sql` (created)
**Fix:** Table with `user_id PRIMARY KEY`, `nonce`, `expires_at`, RLS enabled.

---

## Implementations Completed (Phase 4–7)

### Phase 4 — Calendar + Morning Briefing
| File | Status |
|---|---|
| `services/calendar_service.py` | ✅ Fully implemented — `get_events`, `get_today_events`, `get_upcoming_events`, `create_event`, `get_free_busy`, `find_free_slots` (reads `energy_profile`), `detect_conflicts`, `protect_focus_block` |
| `services/briefing_service.py` | ✅ Fully implemented — `gather_context` (emails + follow-ups + calendar + relationship alerts), `generate_for_user` (Claude text → ElevenLabs audio → Supabase upsert) |
| `jobs/briefing_generator.py` | ✅ Thin wrapper with full exception handling |
| `api/calendar.py` | ✅ 5 endpoints: GET /events, GET /today, POST /events, GET /free-slots, POST /focus-block |
| `api/briefing.py` | ✅ 4 endpoints: GET /today, GET /history, POST /generate (background task), POST /{id}/listened |

### Phase 5 — Follow-up Engine
| File | Status |
|---|---|
| `services/follow_up_engine.py` | ✅ Fully implemented — `process_sent_email` (AI detection, idempotent insert), `check_overdue`, `mark_replied` (auto-close on reply), `draft_follow_up_text` |
| `jobs/follow_up_checker.py` | ✅ Queries overdue, increments reminder_count, logs warnings |
| `api/follow_ups.py` | ✅ 5 endpoints: GET /follow-ups, POST /{id}/send, POST /{id}/close, PATCH /{id}, POST /{id}/draft |
| `api/email.py` | ✅ Wired — POST /{id}/send now fires `follow_up_engine.process_sent_email()` as background task |
| `jobs/triage_worker.py` | ✅ Deleted (dead code — triage done inline in inbox_sync) |

### Phase 6 — Relationship Intelligence
| File | Status |
|---|---|
| `services/relationship_engine.py` | ✅ Fully implemented — `refresh_user` (nightly rebuild), `_rebuild_contact` (strength + sentiment), `update_contact` (lightweight inline), `_compute_strength`, `_compute_sentiment_trend` |
| `services/sentiment_analyser.py` | ✅ `update_contact_trend` implemented — last 10 emails, half-comparison, updates contacts.sentiment_trend |
| `jobs/relationship_updater.py` | ✅ Wired to `relationship_engine.refresh_user()` |
| `api/contacts.py` | ✅ 3 endpoints: GET /contacts, GET /contacts/{email}, PATCH /contacts/{email} |
| `jobs/inbox_sync.py` | ✅ Wired — after each email upsert fires `relationship_engine.update_contact()` and `follow_up_engine.mark_replied()` as background tasks |

### Phase 7 — Polish
| File | Status |
|---|---|
| `jobs/scheduler.py` — digest mode | ✅ `check_digest_mode` job (every 30 min), `_maybe_send_digest` (round to slot, compare against digest_times in user TZ) |
| `infra/migrations/002_phase7_smart_templates.sql` | ✅ Created |
| `api/templates.py` | ✅ Full CRUD + POST /{id}/use with `{{placeholder}}` substitution |
| `main.py` | ✅ Templates router registered |
| `services/voice_service.py` | ✅ Module-level Supabase singleton extracted |
| `frontend/public/manifest.json` | ✅ Created |
| `frontend/public/sw.js` | ✅ Created |
| `frontend/src/app/layout.tsx` | ✅ Created with SW registration + manifest link |

---

## Items Confirmed Correct (no fix needed)

| Item | Status |
|---|---|
| `get_current_user` Depends() on every route | ✅ All routes audited — no exceptions found |
| `get_google_credentials(user_id)` per-request, no global | ✅ Correct in auth middleware and all callers |
| `CalendarService` takes `credentials` param — no global | ✅ Matches Gmail pattern |
| Briefing idempotency — check DB before generating | ✅ `scheduler.py:_maybe_generate_briefing` |
| Briefing time comparison in user's pytz timezone | ✅ `_maybe_generate_briefing` uses `datetime.now(tz)` |
| `briefings` table has `UNIQUE(user_id, date)` | ✅ `schema.sql:267` |
| `contacts` PK is `(email, user_id)` — same sender = separate per user | ✅ `schema.sql:202` |
| All API routes include `user_id` filter in every DB query | ✅ Audited all route files |
| `voice_sessions` log includes `user_id` | ✅ `api/voice.py:184` |
| Claude streaming consumed end-to-end | ✅ `draft_reply()` always consumed via `async for chunk` |
| No `os.getenv()` scattered through service files | ✅ All env access via `settings` object |
| No hardcoded email addresses or user UUIDs | ✅ Grep confirmed none |
| `asyncio.to_thread()` on all Google API `execute()` calls | ✅ All `request.execute` calls wrapped |
| `asyncio.to_thread()` on ElevenLabs TTS | ✅ Both `_generate_sentence_sync` and `_generate_full_sync` |

---

## Revised Confidence Scores

| Phase | Before | After | Notes |
|---|---|---|---|
| **Phase 1** — Auth + Google OAuth | 9/10 | **9/10** | CSRF nonce now fully implemented (connect + callback + migration). Token expiry + blocking refresh bugs fixed. |
| **Phase 2** — Inbox Triage + Drafts | 8/10 | **8/10** | Core pipeline correct. Label cache cross-user bug fixed. Gmail error handling + backoff added. |
| **Phase 3** — Voice Layer | 8/10 | **8/10** | WebSocket pipeline well-designed. JSON parse bugs fixed. Supabase singleton extracted from voice_service. |
| **Phase 4** — Calendar + Briefing | 4/10 | **8/10** | All stubs replaced with full implementations. Calendar service reads energy_profile for free-slot suggestions and focus block protection. Briefing pipeline: context gather → Claude text → ElevenLabs audio → Supabase upsert. |
| **Phase 5** — Follow-up Engine | 3/10 | **8/10** | Full implementation: AI detection on sent emails, overdue checker, mark_replied auto-close, draft generation. Wired into email send and inbox sync. |
| **Phase 6** — Relationship Intelligence | 3/10 | **7/10** | Nightly rebuild + lightweight inline update implemented. Sentiment trend comparison based on email halves. Score held at 7 (not 8) because relationship_strength formula is heuristic and has not been validated against real data. |
| **Phase 7** — Polish | 2/10 | **7/10** | Digest mode scheduler, smart templates CRUD, PWA manifest, service worker, SW registration all implemented. Score held at 7: icon assets still placeholder, digest delivery is log-only (push notification delivery is a TODO), no e2e tests. |

---

## Remaining TODOs

### Before going live

1. **PWA icon assets** — `frontend/public/icon-192.png` and `frontend/public/icon-512.png` are referenced in `manifest.json` but do not exist. Add real PNG files or placeholder SVGs.

2. **Digest delivery** — `_send_digest_for_user` in `scheduler.py` currently logs the digest but does not deliver it (no push notification, no in-app event). Wire to a WebSocket push or Supabase Realtime notification.

3. **`asyncpg` JSONB handling** — Confirm `asyncpg` auto-serialises Python `dict` values for `JSONB` columns. If not, add `json.dumps()` before upsert calls that pass dict values (e.g. `triage_result`, `energy_profile`).

4. **`asyncpg` codec for timezone-aware datetimes** — Confirm `asyncpg` returns `TIMESTAMPTZ` as tz-aware `datetime` objects (it does by default on recent versions). The `token_expiry` parsing in `get_google_credentials` handles both cases defensively.

### Nice to have

5. **Relationship strength calibration** — The `_compute_strength` formula in `relationship_engine.py` is a simple heuristic (`min(1.0, total/100) * recency_factor`). Consider tuning weights against real usage data once the app has users.

6. **Unit tests for scheduler timing** — `_maybe_generate_briefing` and `_maybe_send_digest` compare `HH:MM` strings against `datetime.now(tz)`. These are time-sensitive and hard to test manually; add pytest fixtures that mock `datetime.now`.

7. **`google_callback` error redirect** — On CSRF failure the handler raises `HTTPException(400)` which returns JSON. For a browser-facing redirect route, a `RedirectResponse` to `{FRONTEND_URL}/settings?google_error=csrf` might be more user-friendly.

8. **`creds.expired` 20-second grace window** — `google.oauth2.credentials.Credentials.expired` returns `True` only if `expiry <= utcnow() + timedelta(seconds=20)`. Confirm the 20-second buffer is acceptable for this use case (it is — just document it).

---

## Cannot Verify Without Running the App

1. **Google OAuth full round-trip** — Requires a live GCP project with consent screen configured.
2. **Token auto-refresh** — Needs a real expired token to confirm `asyncio.to_thread(creds.refresh, Request())` path works.
3. **Google STT V2 streaming** — Requires live GCP credentials and an active microphone stream.
4. **ElevenLabs TTS** — Requires valid `ELEVENLABS_API_KEY` and real `FELIX_VOICE_ID`.
5. **WebSocket voice session end-to-end** — Needs browser + mic.
6. **Supabase Storage upload** — Requires live Supabase project with `felix-audio` bucket created and public access enabled.
7. **APScheduler firing in correct time window** — `check_morning_briefings` compares `HH:MM` strings — hard to verify without mocking `datetime.now`.
8. **CSRF nonce expiry edge case** — `oauth_nonces.expires_at` is `TIMESTAMPTZ`. Confirm asyncpg returns a tz-aware datetime so the `expiry.tzinfo is None` guard in `google_callback` works correctly.
