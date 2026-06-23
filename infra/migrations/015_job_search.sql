-- ============================================================
-- Migration 015 — Job Search Mode
-- ============================================================
-- Adds:
--   • settings.job_search_mode  — per-user gate flag (fails closed: off by default,
--                                 no detection runs and the /jobs surface is hidden)
--   • job_applications          — one row per tracked job; primary identity is the
--                                 Gmail thread_id the app was first matched on
--   • job_events                — progress timeline per application (email_in /
--                                 email_out / interview_scheduled / status_change …)
--   • job_suggestions           — low-confidence AI detections awaiting user
--                                 confirmation; confidence + resolved_at double as
--                                 labeled detection outcomes for precision eval
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS guards).
-- ============================================================


-- ── settings.job_search_mode ─────────────────────────────────────────────────
-- Fails closed: off/unset hides the nav item + /jobs page and skips all detection
-- (per the project's fail-closed-on-unset-config convention).

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS job_search_mode BOOLEAN NOT NULL DEFAULT FALSE;


-- ============================================================
-- JOB APPLICATIONS (Feature: Job Search Mode)
-- One row per tracked job. Identity is thread_id first (stable across stages);
-- company+role is display + fuzzy cross-thread stitching only, never a merge key.
-- ============================================================

CREATE TABLE IF NOT EXISTS job_applications (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    thread_id         TEXT,                                   -- Gmail thread first matched on (NULL for manual)
    company           TEXT        NOT NULL,
    role_title        TEXT        NOT NULL,
    location          TEXT,
    job_url           TEXT,
    status            TEXT        NOT NULL DEFAULT 'applied',  -- positive ladder + terminal sinks (see CHECK)
    source            TEXT        NOT NULL DEFAULT 'manual',   -- manual | email | calendar
    contact_name      TEXT,                                   -- primary recruiter/interviewer (display only)
    contact_email     TEXT,                                   -- NOT an identity key (ATS bots / rotating contacts)
    compensation      TEXT,
    notes             TEXT,
    applied_at        TIMESTAMPTZ,
    last_activity_at  TIMESTAMPTZ,
    next_action       TEXT,                                   -- drives the board's due badges
    next_action_at    TIMESTAMPTZ,
    confidence        FLOAT       NOT NULL DEFAULT 1.0,        -- 1.0 manual, model score for AI-created
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('saved','applied','phone_screen','interview','offer','rejected','accepted','withdrawn')),
    CHECK (source IN ('manual','email','calendar'))
);

ALTER TABLE job_applications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own job applications" ON job_applications;
CREATE POLICY "users manage own job applications"
    ON job_applications FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_job_applications_user_status
    ON job_applications (user_id, status);

CREATE INDEX IF NOT EXISTS idx_job_applications_user_next_action
    ON job_applications (user_id, next_action_at);

-- UNIQUE (not just an index): thread_id is the stable identity, and several
-- scanners (inbound loop, spawned sent mirror, catch-up sweeps) can race to
-- create the same thread's job. The partial UNIQUE constraint + INSERT … ON
-- CONFLICT in _upsert_job collapse that race onto one row instead of two
-- duplicate cards. Partial (WHERE thread_id IS NOT NULL) so manual jobs with a
-- NULL thread_id are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_applications_user_thread
    ON job_applications (user_id, thread_id)
    WHERE thread_id IS NOT NULL;


-- ============================================================
-- JOB EVENTS (Feature: Job Search Mode — progress timeline)
-- Append-only timeline per application. De-duped on (user_id, job_id,
-- source_kind, source_id) so inbox-sync replays don't double-log an email.
-- ============================================================

CREATE TABLE IF NOT EXISTS job_events (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    job_id        UUID        NOT NULL REFERENCES job_applications(id) ON DELETE CASCADE,
    event_type    TEXT        NOT NULL,   -- applied|email_in|email_out|interview_scheduled|status_change|note|follow_up_sent
    title         TEXT,
    detail        TEXT,
    source_kind   TEXT,                   -- email | calendar | manual
    source_id     TEXT,                   -- emails.id / sent_emails.id / calendar event id
    occurred_at   TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (event_type IN ('applied','email_in','email_out','interview_scheduled','status_change','note','follow_up_sent'))
);

ALTER TABLE job_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own job events" ON job_events;
CREATE POLICY "users manage own job events"
    ON job_events FOR ALL
    USING (user_id = auth.uid());

-- Partial unique index for idempotent event logging (only when source_id present;
-- manual events have NULL source_id and may legitimately repeat).
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_events_source
    ON job_events (user_id, job_id, source_kind, source_id)
    WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_job_events_user_job_occurred
    ON job_events (user_id, job_id, occurred_at DESC);


-- ============================================================
-- JOB SUGGESTIONS (Feature: Job Search Mode — low-confidence detections)
-- Below the auto floor: surfaced for the user to confirm. confidence + resolved_at
-- are the labeled detection outcomes feeding precision measurement / floor tuning —
-- do not drop them in a cleanup.
-- ============================================================

CREATE TABLE IF NOT EXISTS job_suggestions (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    source_kind      TEXT,                   -- email | calendar
    source_id        TEXT,                   -- emails.id / sent_emails.id / calendar event id
    thread_id        TEXT,                   -- for reconciliation against created/advanced jobs
    company          TEXT,
    role_title       TEXT,
    contact_name     TEXT,
    contact_email    TEXT,
    proposed_status  TEXT,
    proposed_job_id  UUID        REFERENCES job_applications(id) ON DELETE SET NULL,  -- set when it looks like an update
    summary          TEXT,
    confidence       FLOAT,
    status           TEXT        NOT NULL DEFAULT 'pending',  -- pending | accepted | dismissed | auto_dismissed
    resolved_at      TIMESTAMPTZ,            -- set on accept/dismiss; labeled outcome with confidence
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending','accepted','dismissed','auto_dismissed'))
);

ALTER TABLE job_suggestions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own job suggestions" ON job_suggestions;
CREATE POLICY "users manage own job suggestions"
    ON job_suggestions FOR ALL
    USING (user_id = auth.uid());

-- Idempotency: one suggestion per source message.
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_suggestions_source
    ON job_suggestions (user_id, source_kind, source_id)
    WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_job_suggestions_user_thread
    ON job_suggestions (user_id, thread_id);

CREATE INDEX IF NOT EXISTS idx_job_suggestions_user_status
    ON job_suggestions (user_id, status);


-- ============================================================
-- SCAN RETRY TRACKING (Feature: Job Search Mode)
-- Mirrors commitment_scanned_at: a NULL marks an email whose job scan hasn't
-- succeeded yet, so the bounded catch-up sweeps in inbox_sync can retry it.
-- Without this, a transient AI/provider error during a scan would be lost —
-- inbound mail is excluded by the felix-processed label and sent mail is
-- deduped by its PK, so neither would be re-fetched on the next sync.
-- ============================================================

ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS job_scanned_at TIMESTAMPTZ;

ALTER TABLE sent_emails
    ADD COLUMN IF NOT EXISTS job_scanned_at TIMESTAMPTZ;
