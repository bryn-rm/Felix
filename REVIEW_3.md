# Felix API Audit — REVIEW_3.md

> Read-only audit of FastAPI backend ↔ Next.js frontend alignment.
> No files were modified to produce this report.

---

## CATEGORY 1 — URL AND METHOD MISMATCHES

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/lib/api.ts:81` ↔ `backend/app/api/email.py:201`
**Problem:** `streamDraft` appends `/stream` to the draft URL, which does not match any registered route.
**Frontend has:**
```typescript
const res = await fetch(`${API_BASE}/emails/${emailId}/draft/stream`, {
  method: "POST",
```
**Backend has:**
```python
@router.post("/{email_id}/draft")
async def generate_draft(...):
    return StreamingResponse(sse_stream(), media_type="text/event-stream")
```
**Fix:** Change frontend URL to `/emails/${emailId}/draft` (remove `/stream` suffix).

---

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/hooks/useDraft.ts:74` ↔ `backend/app/api/email.py`
**Problem:** `useDraft` calls `GET /emails/{id}/draft` which does not exist; the backend only exposes `GET /emails/{id}` (which embeds the draft).
**Frontend has:**
```typescript
const existing = await api.get<Draft>(`/emails/${emailId}/draft`);
```
**Backend has:** No `GET /emails/{id}/draft` route. Nearest route is `GET /emails/{email_id}` returning `{...email, "draft": draft_row_or_null}`.
**Fix:** Change to `api.get<{draft: Draft | null}>(`/emails/${emailId}`)` and read `.draft`.

---

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/hooks/useDraft.ts:145` ↔ `backend/app/api/email.py:419`
**Problem:** `discard` calls `DELETE /drafts/{draft.id}` but no `/drafts/` router exists; the backend route is scoped under `/emails`.
**Frontend has:**
```typescript
await api.del(`/drafts/${draft.id}`);
```
**Backend has:**
```python
@router.delete("/{email_id}/draft")   # mounted at /emails
async def discard_draft(email_id: str, ...):
```
**Fix:** Change to `api.del(`/emails/${emailId}/draft`)` using the email ID (available in the hook via its `emailId` param).

---

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/settings/page.tsx:442` ↔ `backend/app/api/auth.py:255`
**Problem:** Google disconnect calls the wrong URL — missing `/disconnect` suffix.
**Frontend has:**
```typescript
await api.del("/auth/google");
```
**Backend has:**
```python
@router.delete("/google/disconnect")
async def disconnect_google(...):
```
**Fix:** Change frontend URL to `/auth/google/disconnect`.

---

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/settings/page.tsx:465` ↔ `backend/app/api/settings.py`, `backend/app/api/polish.py`
**Problem:** `POST /settings/analyse-style` does not exist anywhere in the backend.
**Frontend has:**
```typescript
await api.post("/settings/analyse-style");
```
**Backend has:** No such endpoint in `settings.py` or `polish.py` (polish routes are `/polish/digest`, `/polish/weekly-review`, `/polish/templates/suggestions`, `/polish/style-evolution`).
**Fix:** Either add `POST /settings/analyse-style` to the backend (wrapping polish service logic), or wire the frontend to an existing `/polish/*` endpoint.

---

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/hooks/useCalendar.ts:22` ↔ `backend/app/api/calendar.py:51`
**Problem:** `useCalendar` passes `?start=&end=` date params which the backend ignores entirely.
**Frontend has:**
```typescript
useSWR(`/calendar/events?start=${start}&end=${end}`, ...)
```
**Backend has:**
```python
@router.get("/events")
async def list_events(
    days_ahead: int = Query(7, ge=1, le=30),
    ...
```
**Fix:** Frontend should pass `?days_ahead=14` (or similar) and filter events client-side by `weekStart`–`weekStart+6`. Remove `start`/`end` params.

---

### [CAT-1] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/calendar/page.tsx:97` ↔ `backend/app/api/calendar.py:147`
**Problem:** Free-slot modal sends `?date=&duration=` but backend accepts `?duration_minutes=&days_ahead=`.
**Frontend has:**
```typescript
api.get<FreeSlot[]>(`/calendar/free-slots?date=${today}&duration=30`)
```
**Backend has:**
```python
@router.get("/free-slots")
async def get_free_slots(
    duration_minutes: int = Query(30, ge=15, le=480),
    days_ahead: int = Query(5, ge=1, le=14),
    ...
```
**Fix:** Change to `/calendar/free-slots?duration_minutes=30&days_ahead=1` (use `days_ahead=1` to approximate "today").

---

### [CAT-1] — Severity: HIGH
**File:** `frontend/src/components/follow-ups/FollowUpCard.tsx:81` ↔ `backend/app/api/follow_ups.py:152`
**Problem:** Frontend sends `PATCH` to close a follow-up but backend close endpoint is `POST /{id}/close`.
**Frontend has:**
```typescript
await api.patch(`/follow-ups/${followUp.id}`, { status: "closed" });
```
**Backend has:**
```python
@router.post("/{follow_up_id}/close")
async def close_follow_up(...):
```
**Fix:** Change to `api.post(`/follow-ups/${followUp.id}/close`)` with no body.

---

### [CAT-1] — Severity: HIGH
**File:** `frontend/src/app/(app)/contacts/[email]/page.tsx:197` ↔ `backend/app/api/email.py:59`
**Problem:** Contact profile fetches `/emails/?contact=…` but `GET /emails` accepts no `contact` param.
**Frontend has:**
```typescript
useSWR(`/emails/?contact=${encodeURIComponent(email)}&limit=10`, ...)
```
**Backend has:**
```python
@router.get("")
async def list_emails(
    category: str | None = Query(None),
    urgency: str | None = Query(None),
    draft_pending: bool | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    ...
```
**Fix:** Add `from_email: str | None = Query(None)` param to `GET /emails`, or use the existing contact profile endpoint's embedded `recent_emails` array.

---

### [CAT-1] — Severity: HIGH
**File:** `frontend/src/app/(app)/contacts/[email]/page.tsx:202` ↔ `backend/app/main.py`
**Problem:** Frontend calls `GET /meetings/` but no meetings router is registered in `main.py`.
**Frontend has:**
```typescript
useSWR(`/meetings/?contact=${encodeURIComponent(email)}`, ...)
```
**Backend has:** No `/meetings` prefix registered. `main.py` registers: auth, email, calendar, voice, contacts, follow_ups, briefing, polish, settings, templates, eval, admin.
**Fix:** Use the `recent_meetings` array already returned by `GET /contacts/{email}` (see CAT-3 fix for that endpoint), or add a `/meetings` router.

---

## CATEGORY 2 — REQUEST BODY MISMATCHES

### [CAT-2] — Severity: HIGH
**File:** `frontend/src/components/follow-ups/FollowUpCard.tsx:97` ↔ `backend/app/api/follow_ups.py:178`
**Problem:** Snooze sends `{follow_up_by: date}` via `PATCH` but `FollowUpPatch` only accepts `{auto_draft: str}`.
**Frontend has:**
```typescript
await api.patch(`/follow-ups/${followUp.id}`, {
  follow_up_by: snoozeDate,
});
```
**Backend has:**
```python
class FollowUpPatch(BaseModel):
    auto_draft: str
```
**Fix:** Add `follow_up_by: str | None = None` to `FollowUpPatch` and handle the update in `update_follow_up`; or add a dedicated `POST /follow-ups/{id}/snooze` endpoint.

---

### [CAT-2] — Severity: HIGH
**File:** `frontend/src/app/(app)/settings/page.tsx:401` ↔ `backend/app/api/settings.py:29`
**Problem:** VIP contacts are saved via `PATCH /settings` with `{vip_contacts: [...]}` but `SettingsUpdate` does not include `vip_contacts`.
**Frontend has:**
```typescript
await api.patch("/settings", { vip_contacts: updated });
```
**Backend has:**
```python
class SettingsUpdate(BaseModel):
    display_name: str | None = None
    timezone: str | None = None
    briefing_time: str | None = None
    digest_mode: bool | None = None
    digest_times: list[str] | None = None
    energy_profile: dict | None = None
    felix_voice_id: str | None = None
    # ← no vip_contacts field
```
**Fix:** Change frontend to `api.patch("/settings/vip-contacts", { vip_contacts: updated })` using the existing `PUT /settings/vip-contacts` endpoint; note the method is `PUT` not `PATCH`.

---

## CATEGORY 3 — RESPONSE SHAPE MISMATCHES

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/hooks/useEmails.ts:28` ↔ `backend/app/api/email.py:110`
**Problem:** Frontend expects `{emails, total}` but backend does not return a `total` field; infinite scroll is permanently broken.
**Frontend has:**
```typescript
interface EmailsPage {
  emails: Email[];
  total: number;      // ← expected
}
const total: number = data?.[0]?.total ?? 0;  // always 0
```
**Backend has:**
```python
return {"emails": rows, "limit": limit, "offset": offset}
# ← no "total" key
```
**Fix:** Add `"total": total_count` to the backend response (requires a `COUNT(*)` subquery), or replace infinite scroll with cursor-based pagination.

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/hooks/useFollowUps.ts:23` ↔ `backend/app/api/follow_ups.py:65`
**Problem:** Hook expects bare `FollowUp[]` but backend wraps the array; `.filter()` is called on a plain object → TypeError at runtime.
**Frontend has:**
```typescript
useSWR<FollowUp[]>("/follow-ups/", (url) => api.get<FollowUp[]>(url))
const all = data ?? [];    // data = { follow_ups: [...], count: N }
all.filter(...)             // TypeError: all.filter is not a function
```
**Backend has:**
```python
return {"follow_ups": rows, "count": len(rows)}
```
**Fix:** Change fetcher to unwrap: `api.get<{follow_ups: FollowUp[]}>(url).then(r => r.follow_ups)`. Also note the hook calls `/follow-ups/` with no `?status=` param; the backend defaults to `status=waiting`, so "closed" follow-ups are never fetched. Pass `?status=waiting` explicitly or rethink to fetch all and filter client-side (requires removing the status validation in the backend, or making multiple requests).

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/briefing/page.tsx:236` ↔ `backend/app/api/briefing.py:31`
**Problem:** Briefing page binds `todayBriefing.text` directly but backend wraps the row in `{briefing: row}`.
**Frontend has:**
```typescript
useSWR<Briefing | null>("/briefing/today", ...)
// uses: todayBriefing?.text, todayBriefing?.audio_url, todayBriefing?.id
```
**Backend has:**
```python
return {"briefing": row}
# or
return {"briefing": None, "message": "No briefing generated yet today."}
```
**Fix:** Change type to `useSWR<{briefing: Briefing | null}>` and access `data?.briefing`.

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/briefing/page.tsx:241` ↔ `backend/app/api/briefing.py:59`
**Problem:** History SWR expects bare `Briefing[]` but backend returns wrapped object; `history.map(...)` fails.
**Frontend has:**
```typescript
useSWR<Briefing[]>("/briefing/history", ...)
// uses: history.slice(0, 7).map((b) => ...)
```
**Backend has:**
```python
return {"briefings": rows, "count": len(rows)}
```
**Fix:** Change type to `{briefings: Briefing[]}` and access `data?.briefings`.

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/contacts/page.tsx:131` ↔ `backend/app/api/contacts.py:76`
**Problem:** Contacts page expects bare `Contact[]` but backend returns a wrapped object; the grid never renders.
**Frontend has:**
```typescript
const { data, isLoading, error } = useSWR<Contact[]>(
  "/contacts/",
  (url) => api.get<Contact[]>(url),
);
const contacts = useMemo(() => {
  let list = data ?? [];    // data = { contacts: [...], ... } → list = {}
  ...list.sort(...)         // { }.sort is not a function
```
**Backend has:**
```python
return {"contacts": rows, "count": len(rows), "limit": limit, "offset": offset}
```
**Fix:** Change type to `{contacts: Contact[]}` and use `data?.contacts ?? []`.

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/contacts/[email]/page.tsx:192` ↔ `backend/app/api/contacts.py:126`
**Problem:** Contact profile page destructures the response as if it is a `ContactProfile` directly, but backend wraps it; all fields are undefined and `contact.topics_discussed.length` throws.
**Frontend has:**
```typescript
useSWR<ContactProfile>(
  `/contacts/${encodeURIComponent(email)}`,
  (url) => api.get<ContactProfile>(url),
)
// then: contact.vip, contact.topics_discussed, contact.open_commitments, etc.
```
**Backend has:**
```python
return {
    "contact": contact,
    "recent_emails": recent_emails,
    "recent_meetings": recent_meetings,
}
```
**Fix:** Change type to `{contact: ContactProfile; recent_emails: Email[]; recent_meetings: Meeting[]}` and access `data.contact`, `data.recent_emails`, `data.recent_meetings`. Remove the separate `/emails/?contact=` and `/meetings/?contact=` SWR calls (data is already embedded).

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/templates/page.tsx:88` ↔ `backend/app/api/templates.py:67`
**Problem:** Templates page expects bare `Template[]` but backend returns a wrapped object; the list always renders as empty.
**Frontend has:**
```typescript
useSWR<Template[]>("/templates/", (url) => api.get<Template[]>(url))
// uses: templates.length > 0, templates.map(...)
// templates.length = undefined → conditional never true → empty state shown
```
**Backend has:**
```python
return {"templates": rows, "count": len(rows)}
```
**Fix:** Change type to `{templates: Template[]}` and use `data?.templates ?? []`.

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/calendar/page.tsx:96` ↔ `backend/app/api/calendar.py:172`
**Problem:** Free-slot modal expects bare `FreeSlot[]` but backend returns wrapped object; slot list never renders.
**Frontend has:**
```typescript
api.get<FreeSlot[]>(`/calendar/free-slots?...`).then(setSlots)
// uses: slots.map((slot, i) => ...)
```
**Backend has:**
```python
return {"slots": slots, "duration_minutes": duration_minutes}
```
**Fix:** Change to `api.get<{slots: FreeSlot[]}>(...).then(r => setSlots(r.slots))`.

---

### [CAT-3] — Severity: CRITICAL
**File:** `frontend/src/hooks/useCalendar.ts:21` ↔ `backend/app/api/calendar.py:69`
**Problem:** `useCalendar` expects bare `CalendarEvent[]` but backend wraps events; WeekGrid receives an object instead of an array.
**Frontend has:**
```typescript
useSWR<CalendarEvent[]>(`/calendar/events?...`, ...)
return { events: data ?? [], ... }
// data ?? [] = { events: [...], count: N }
```
**Backend has:**
```python
return {"events": events, "count": len(events)}
```
**Fix:** Change type to `{events: CalendarEvent[]}` and return `data?.events ?? []`.

---

### [CAT-3] — Severity: HIGH
**File:** `frontend/src/app/(app)/settings/page.tsx:275` ↔ `backend/app/api/auth.py:234`
**Problem:** `GoogleStatus.email` is undefined because the backend returns the field as `google_email`.
**Frontend has:**
```typescript
interface GoogleStatus {
  connected: boolean;
  email?: string;       // ← "email"
}
// used as: googleStatus.email
```
**Backend has:**
```python
return {
    "connected": True,
    "google_email": row["google_email"],   # ← "google_email"
    "connected_at": row["connected_at"],
    "last_sync": row["last_sync"],
}
```
**Fix:** Rename `GoogleStatus.email` to `google_email`, or rename the backend key to `email`.

---

## CATEGORY 4 — AUTH HEADER MISMATCHES

No auth header mismatches found. All HTTP calls funnel through `api.ts:request()` which always attaches `Authorization: Bearer <session.access_token>`. The WebSocket voice connection correctly sends the JWT as the first text message `{"token": "<jwt>"}` matching the backend `_authenticate_ws` helper.

---

## CATEGORY 5 — MISSING ENDPOINTS

### [CAT-5] — Severity: CRITICAL
**File:** `frontend/src/lib/api.ts:81` ↔ backend
**Problem:** `POST /emails/{id}/draft/stream` does not exist. (See CAT-1 fix.)

### [CAT-5] — Severity: CRITICAL
**File:** `frontend/src/hooks/useDraft.ts:74` ↔ backend
**Problem:** `GET /emails/{id}/draft` does not exist. (See CAT-1 fix.)

### [CAT-5] — Severity: CRITICAL
**File:** `frontend/src/hooks/useDraft.ts:145` ↔ backend
**Problem:** `DELETE /drafts/{id}` does not exist. (See CAT-1 fix.)

### [CAT-5] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/settings/page.tsx:465` ↔ backend
**Problem:** `POST /settings/analyse-style` does not exist in any registered router. (See CAT-1 fix.)

### [CAT-5] — Severity: HIGH
**File:** `frontend/src/app/(app)/contacts/[email]/page.tsx:202` ↔ backend
**Problem:** `GET /meetings/` router is not registered in `main.py`. (See CAT-1 fix.)

### [CAT-5] — Severity: LOW
**File:** backend only ↔ `frontend/src/app/(app)/settings/page.tsx`
**Problem:** `PUT /settings/vip-contacts` exists on the backend but the frontend never calls it; it uses `PATCH /settings` instead (which ignores the field). The backend endpoint is effectively dead.
**Fix:** Wire frontend VIP save to `api.patch("/settings/vip-contacts", { vip_contacts: updated })` — note: method should match; backend uses `PUT`, not `PATCH`.

---

## CATEGORY 6 — ERROR HANDLING GAPS

### [CAT-6] — Severity: MEDIUM
**File:** `frontend/src/lib/api.ts:51` — no 401 global redirect
**Problem:** `request()` throws `ApiError(401, ...)` but no layout or global boundary catches it to redirect to `/login`. On session expiry, users see broken pages with raw error text instead of being sent to sign in.
**Frontend has:**
```typescript
if (!res.ok) {
  throw new ApiError(res.status, message);
}
```
**Fix:** Add an SWR global error handler or Next.js middleware that redirects to `/login` on `ApiError.status === 401`.

### [CAT-6] — Severity: MEDIUM
**File:** Multiple pages — no 403 redirect to `/connect`
**Problem:** When Google is not connected, several endpoints (email generate, calendar, contacts) return 403. No page handles 403 to redirect the user to `/connect`.
**Fix:** Add 403-specific handling in `request()` (or per-feature) to redirect to `/connect`.

### [CAT-6] — Severity: LOW
**File:** `frontend/src/app/(app)/contacts/page.tsx:212`
**Problem:** Error rendered as `(error as Error).message` — loses status code context; a 401 and a network error look identical to the user.
**Fix:** Use `error instanceof ApiError ? error.status : null` to branch.

### [CAT-6] — Severity: LOW
**File:** `frontend/src/components/email/DraftPanel.tsx:67`
**Problem:** Error state shows a "Retry" button that reloads the whole page (`window.location.reload()`). After the draft URL fix (CAT-1-C1), the error surface will shrink, but a targeted retry (re-call `startStream`) would be more appropriate.

---

## CATEGORY 7 — TYPE SAFETY GAPS

### [CAT-7] — Severity: CRITICAL
**File:** `frontend/src/app/(app)/contacts/[email]/page.tsx:394`
**Problem:** `contact.topics_discussed.length` crashes if `contact` is actually the full `{contact, recent_emails, recent_meetings}` response object (`.topics_discussed` is `undefined`).
**Frontend has:**
```typescript
{contact.topics_discussed.length === 0 ? (   // TypeError if undefined
```
**Fix:** Unwrap `data.contact` as described in CAT-3.

### [CAT-7] — Severity: CRITICAL
**File:** `frontend/src/hooks/useFollowUps.ts:29`
**Problem:** `data ?? []` is assigned to `all`, but if `data` is `{follow_ups: [...], count: N}`, then `all` is an object; `all.filter(...)` and `all.length` throw/return garbage.
**Frontend has:**
```typescript
const all = data ?? [];
const followUps = filter === "all" ? all : all.filter(...);
```
**Fix:** Unwrap as in CAT-3.

### [CAT-7] — Severity: HIGH
**File:** `frontend/src/app/(app)/contacts/page.tsx:131`
**Problem:** After `useSWR<Contact[]>`, `data` is actually `{contacts: [...]}` at runtime; TypeScript's generic is a lie. Any `.length`, `.sort()`, `.filter()` on `data` fails silently or throws.

### [CAT-7] — Severity: HIGH
**File:** `frontend/src/app/(app)/briefing/page.tsx:358`
**Problem:** `todayBriefing.text`, `todayBriefing.audio_url`, `todayBriefing.id` are all `undefined` because `todayBriefing` is `{briefing: row}` at runtime. Page renders nothing meaningful.

### [CAT-7] — Severity: MEDIUM
**File:** `frontend/src/hooks/useEmails.ts:35`
**Problem:** `total` typed as `number` but is always `0` at runtime (no backend field). `hasMore` is always `false`. Calling `loadMore()` silently does nothing.
```typescript
const total: number = data?.[0]?.total ?? 0;  // always 0
```

---

## CATEGORY 8 — ENVIRONMENT VARIABLE USAGE

No environment variable mismatches found.

- `frontend/src/lib/api.ts:3`: correctly uses `NEXT_PUBLIC_API_URL ?? ""`
- `frontend/src/hooks/useVoice.ts:80–86`: correctly derives WebSocket base from `NEXT_PUBLIC_API_URL`, with `window.location.origin` fallback for same-origin deploys and `ws://localhost:8000` for SSR/dev. No hardcoded production URLs found.

---

## Summary Table

| Endpoint | Method | URL Match | Body Match | Response Match | Auth Header | Status |
|---|---|---|---|---|---|---|
| GET /auth/google/connect | GET | ✅ | — | ✅ | ✅ | ✅ PASS |
| GET /auth/google/callback | GET | ✅ | — | ✅ (redirect) | — | ✅ PASS |
| GET /auth/google/status | GET | ✅ | — | ⚠️ (`google_email` vs `email`) | ✅ | ⚠️ PARTIAL |
| DELETE /auth/google/disconnect | DELETE | ❌ (frontend: `/auth/google`) | — | — | ✅ | ❌ FAIL |
| GET /emails | GET | ✅ | — | ⚠️ (no `total` field) | ✅ | ⚠️ PARTIAL |
| GET /emails/stats | GET | ✅ | — | ✅ | ✅ | ✅ PASS |
| GET /emails/{id} | GET | ✅ | — | ✅ | ✅ | ✅ PASS |
| GET /emails/{id}/thread | GET | ✅ | — | ✅ | ✅ | ✅ PASS |
| POST /emails/{id}/draft (SSE stream) | POST | ❌ (frontend appends `/stream`) | — | — | ✅ | ❌ FAIL |
| GET /emails/{id}/draft (load existing) | GET | ❌ (route doesn't exist) | — | — | ✅ | ❌ FAIL |
| PATCH /emails/{id}/draft | PATCH | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| POST /emails/{id}/send | POST | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| DELETE /emails/{id}/draft | DELETE | ❌ (frontend: `DELETE /drafts/{id}`) | — | — | ✅ | ❌ FAIL |
| POST /eval/feedback | POST | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| GET /calendar/events | GET | ✅ | ❌ (`start/end` ignored) | ❌ (wrapped `events`) | ✅ | ❌ FAIL |
| GET /calendar/today | GET | ✅ | — | ✅ | ✅ | ✅ PASS |
| POST /calendar/events | POST | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| GET /calendar/free-slots | GET | ✅ | ❌ (`date`/`duration` vs `duration_minutes`/`days_ahead`) | ❌ (wrapped `slots`) | ✅ | ❌ FAIL |
| POST /calendar/focus-block | POST | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| GET /follow-ups | GET | ✅ | — | ❌ (wrapped `follow_ups`; no all-status fetch) | ✅ | ❌ FAIL |
| POST /follow-ups/{id}/send | POST | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| POST /follow-ups/{id}/close | POST | ❌ (frontend: `PATCH` with `{status}`) | ❌ | — | ✅ | ❌ FAIL |
| PATCH /follow-ups/{id} | PATCH | ✅ | ⚠️ (`follow_up_by` not in schema) | ✅ | ✅ | ⚠️ PARTIAL |
| POST /follow-ups/{id}/draft | POST | ✅ | — | ✅ | ✅ | ✅ PASS |
| GET /contacts | GET | ✅ | — | ❌ (wrapped `contacts`) | ✅ | ❌ FAIL |
| GET /contacts/{email} | GET | ✅ | — | ❌ (wrapped `{contact, recent_emails, recent_meetings}`) | ✅ | ❌ FAIL |
| PATCH /contacts/{email} | PATCH | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| GET /briefing/today | GET | ✅ | — | ❌ (wrapped `.briefing`) | ✅ | ❌ FAIL |
| GET /briefing/history | GET | ✅ | — | ❌ (wrapped `.briefings`) | ✅ | ❌ FAIL |
| POST /briefing/generate | POST | ✅ | — | ✅ | ✅ | ✅ PASS |
| POST /briefing/{id}/listened | POST | ✅ | — | ✅ | ✅ | ✅ PASS |
| GET /settings | GET | ✅ | — | ✅ | ✅ | ✅ PASS |
| PATCH /settings | PATCH | ✅ | ⚠️ (`vip_contacts` silently ignored) | ✅ | ✅ | ⚠️ PARTIAL |
| PUT /settings/vip-contacts | PUT | ❌ (never called by frontend) | — | — | — | ❌ FAIL |
| POST /settings/analyse-style | POST | ❌ (no such route) | — | — | ✅ | ❌ FAIL |
| GET /templates | GET | ✅ | — | ❌ (wrapped `templates`) | ✅ | ❌ FAIL |
| POST /templates | POST | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| PATCH /templates/{id} | PATCH | ✅ | ✅ | ✅ | ✅ | ✅ PASS |
| DELETE /templates/{id} | DELETE | ✅ | — | ✅ | ✅ | ✅ PASS |
| WS /voice/stream | WS | ✅ | ✅ (JWT first msg) | ✅ | ✅ | ✅ PASS |
| GET /emails?contact=… | GET | ❌ (param ignored by backend) | — | — | ✅ | ❌ FAIL |
| GET /meetings?contact=… | GET | ❌ (router not registered) | — | — | ✅ | ❌ FAIL |

---

## Overall Verdict

- **Total endpoints audited:** 42
- **Critical issues:** 15 — block the feature from working at all
- **High issues:** 7 — features broken, app still loads
- **Medium issues:** 6 — degraded or missing experience
- **Low issues:** 4 — edge cases and cosmetic

### Is the app ready to run end-to-end? **NO**

### Minimum fixes required before the first real test (priority order):

1. **`api.ts streamDraft`** — remove `/stream` suffix from URL (`/emails/${emailId}/draft`)
2. **`useDraft.init`** — call `GET /emails/${emailId}` and read the nested `.draft` field; remove the non-existent `GET /emails/{id}/draft` call
3. **`useDraft.discard`** — change `DELETE /drafts/${draft.id}` to `DELETE /emails/${emailId}/draft`
4. **`useCalendar`** — remove `?start=&end=` params, add `?days_ahead=14`, unwrap `data.events`
5. **`useFollowUps`** — unwrap `data.follow_ups` from the backend response
6. **Briefing page** — unwrap `data.briefing` (today) and `data.briefings` (history)
7. **Contacts list** — unwrap `data.contacts`
8. **Contact profile** — unwrap `data.contact`; use embedded `data.recent_emails` and `data.recent_meetings` instead of the broken `/emails/?contact=` and `/meetings/?contact=` calls
9. **Templates list** — unwrap `data.templates`
10. **Calendar free-slots** — change `?date=&duration=` to `?duration_minutes=30`, unwrap `data.slots`
11. **Settings disconnect** — change `api.del("/auth/google")` to `api.del("/auth/google/disconnect")`
12. **Settings VIP contacts** — change `api.patch("/settings", {vip_contacts})` to `api.patch("/settings/vip-contacts", {vip_contacts})` (method is `PUT` on backend, change accordingly)
13. **Follow-up close** — change `api.patch(.., {status:"closed"})` to `api.post(.../close)`
14. **Follow-up snooze** — add `follow_up_by` field to backend `FollowUpPatch` model
15. **Analyse style** — add `POST /settings/analyse-style` to backend (or wire to an existing `/polish/*` route)
