-- ============================================================
-- Migration 008 — Meeting Prep + Commitment Radar
-- ============================================================
-- Adds:
--   • settings.meeting_prep_mode — per-user toggle for prep delivery surfaces
--   • meeting_preps              — cached pre-meeting prep cards
--   • sent_emails                — Gmail "in:sent" mirror so commitment detection
--                                  and weekly stats see messages typed directly
--                                  in Gmail (not just Felix-assisted drafts)
--   • commitments                — promises in either direction, extracted by
--                                  Claude from inbound + sent email
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent (IF NOT EXISTS / IF NOT EXISTS guards).
-- ============================================================


-- ── settings.meeting_prep_mode ───────────────────────────────────────────────
-- off | email_only | in_app_only | both — defaults to in_app_only so we don't
-- send unsolicited emails on first run.

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS meeting_prep_mode TEXT NOT NULL DEFAULT 'in_app_only';


-- ============================================================
-- MEETING PREPS (Feature: Meeting Prep)
-- One row per (user, calendar event) — generated 5–15 min before each meeting.
-- Cached HTML + plaintext + a structured snapshot for in-app rendering.
-- ============================================================

CREATE TABLE IF NOT EXISTS meeting_preps (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    event_id        TEXT        NOT NULL,            -- Google Calendar event id
    event_title     TEXT,
    event_start     TIMESTAMPTZ,
    attendees       TEXT[]      NOT NULL DEFAULT '{}',
    content_html    TEXT,                            -- shell-wrapped HTML body
    content_text    TEXT,                            -- plaintext for voice / fallback
    status          TEXT        NOT NULL DEFAULT 'generated',  -- generated | sent | skipped | failed
    delivery_modes  TEXT[]      NOT NULL DEFAULT '{}',          -- which surfaces fired (email/in_app)
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, event_id)
);

ALTER TABLE meeting_preps ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own meeting preps" ON meeting_preps;
CREATE POLICY "users manage own meeting preps"
    ON meeting_preps FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_meeting_preps_user_start
    ON meeting_preps (user_id, event_start DESC);


-- ============================================================
-- SENT EMAILS (Feature: Commitment Radar — Gmail "in:sent" mirror)
-- Felix only stored Felix-assisted drafts before this. The sent mirror gives
-- visibility into messages typed directly in Gmail so commitment detection
-- and weekly stats are accurate.
-- ============================================================

CREATE TABLE IF NOT EXISTS sent_emails (
    id                  TEXT        NOT NULL,             -- Gmail message id
    user_id             UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    thread_id           TEXT,
    message_id_header   TEXT,
    from_email          TEXT,
    -- Recipient arrays so group threads (To: a@x, b@y, c@z) preserve all
    -- attendees. Commitment detection picks a primary counterparty from the
    -- list; meeting-prep / email-history reads use the full set.
    to_emails           TEXT[]      NOT NULL DEFAULT '{}',
    to_names            TEXT[]      NOT NULL DEFAULT '{}',
    subject             TEXT,
    body                TEXT,
    snippet             TEXT,
    sent_at             TIMESTAMPTZ,
    processed_at        TIMESTAMPTZ,                       -- set after commitment scan
    PRIMARY KEY (id, user_id)
);

ALTER TABLE sent_emails ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own sent emails" ON sent_emails;
CREATE POLICY "users manage own sent emails"
    ON sent_emails FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_sent_emails_user_sent
    ON sent_emails (user_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_sent_emails_user_thread
    ON sent_emails (user_id, thread_id);


-- ============================================================
-- COMMITMENTS (Feature: Commitment Radar)
-- Promises in either direction extracted by Claude from inbound + sent email.
-- Lights up the previously-unused contacts.open_commitments / their_open_commitments
-- columns and the memory_episodes episode_type='commitment' slot.
-- ============================================================

CREATE TABLE IF NOT EXISTS commitments (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    source_email_id     TEXT,                                -- emails.id or sent_emails.id
    source_kind         TEXT        NOT NULL,                -- 'inbound' | 'sent'
    direction           TEXT        NOT NULL,                -- 'owed_by_user' | 'owed_to_user'
    counterparty_email  TEXT,
    counterparty_name   TEXT,
    text                TEXT        NOT NULL,                -- the commitment, in their words
    source_quote        TEXT,                                -- exact substring from the email
    deadline            TIMESTAMPTZ,
    confidence          FLOAT       NOT NULL DEFAULT 0.5,    -- 0.0 → 1.0 from Claude
    status              TEXT        NOT NULL DEFAULT 'open', -- open | done | dropped | rescued
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    CHECK (direction IN ('owed_by_user','owed_to_user')),
    CHECK (source_kind IN ('inbound','sent')),
    CHECK (status     IN ('open','done','dropped','rescued'))
);

ALTER TABLE commitments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own commitments" ON commitments;
CREATE POLICY "users manage own commitments"
    ON commitments FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_commitments_user_status_deadline
    ON commitments (user_id, status, deadline);

CREATE INDEX IF NOT EXISTS idx_commitments_user_counterparty
    ON commitments (user_id, counterparty_email);
