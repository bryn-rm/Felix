-- ============================================================
-- Felix — Supabase PostgreSQL Schema
-- ============================================================
-- Run this in Supabase: Dashboard → SQL Editor → New query
--
-- Every table has:
--   • user_id UUID referencing auth.users (Supabase Auth)
--   • ALTER TABLE … ENABLE ROW LEVEL SECURITY
--   • A policy that restricts rows to the owning user
--
-- RLS is a safety net for the anon-key frontend path.
-- The backend uses the service key (bypasses RLS) but MUST
-- always include user_id in every query.
-- ============================================================


-- ============================================================
-- GOOGLE CONNECTIONS (one row per user)
-- Stores encrypted OAuth tokens for Gmail + Calendar access.
-- ============================================================

CREATE TABLE IF NOT EXISTS google_connections (
    user_id       UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    google_email  TEXT NOT NULL,
    access_token  TEXT NOT NULL,       -- encrypted with Fernet (TOKEN_ENCRYPTION_KEY)
    refresh_token TEXT NOT NULL,       -- encrypted with Fernet
    token_expiry  TIMESTAMPTZ,
    connected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sync     TIMESTAMPTZ
);

ALTER TABLE google_connections ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own google connection"
    ON google_connections FOR ALL
    USING (user_id = auth.uid());


-- ============================================================
-- USER SETTINGS (one row per user)
-- All per-user configuration lives here — no hardcoded env vars.
-- ============================================================

CREATE TABLE IF NOT EXISTS settings (
    user_id        UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name   TEXT,
    timezone       TEXT        NOT NULL DEFAULT 'Europe/London',
    briefing_time  TIME        NOT NULL DEFAULT '07:30',
    style_profile  JSONB,                          -- StyleProfile dataclass as JSON
    vip_contacts   TEXT[]      NOT NULL DEFAULT '{}',
    digest_mode    BOOLEAN     NOT NULL DEFAULT FALSE,
    digest_times   TEXT[]      NOT NULL DEFAULT '{}',  -- e.g. ["08:00","12:00","18:00"]
    energy_profile JSONB,                          -- deep work windows, meeting windows
    felix_voice_id TEXT,                           -- per-user ElevenLabs voice override (optional)
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own settings"
    ON settings FOR ALL
    USING (user_id = auth.uid());


-- ============================================================
-- EMAILS
-- Local mirror of inbox emails processed by Felix.
-- Gmail is the source of truth; this is the triage-enriched cache.
-- ============================================================

CREATE TABLE IF NOT EXISTS emails (
    id                  TEXT    NOT NULL,
    user_id             UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    thread_id           TEXT,
    message_id_header   TEXT,   -- RFC 2822 Message-ID, used for In-Reply-To when sending
    from_email          TEXT,
    from_name           TEXT,
    to_email            TEXT,
    subject             TEXT,
    body                TEXT,
    snippet             TEXT,
    received_at         TIMESTAMPTZ,
    -- Triage metadata set by Claude
    category            TEXT,   -- action_required | fyi | waiting_on | newsletter | automated | vip
    urgency             TEXT,   -- low | medium | high | critical
    sentiment           TEXT,   -- neutral | positive | stressed | frustrated | urgent
    topic               TEXT,
    triage_json         JSONB,  -- full triage response for future use
    processed_at        TIMESTAMPTZ,
    draft_generated     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (id, user_id)
);

ALTER TABLE emails ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users see own emails"
    ON emails FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_emails_user_received
    ON emails (user_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_emails_user_category
    ON emails (user_id, category);


-- ============================================================
-- DRAFTS
-- AI-generated reply drafts awaiting user review.
-- ============================================================

CREATE TABLE IF NOT EXISTS drafts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email_id     TEXT,
    draft_text   TEXT,
    status       TEXT        NOT NULL DEFAULT 'pending',  -- pending | approved | sent | discarded
    edited_text  TEXT,       -- user's edits before sending
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at      TIMESTAMPTZ
);

ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own drafts"
    ON drafts FOR ALL
    USING (user_id = auth.uid());

-- One draft per (email, user) — enforced here so inbox_sync upserts are safe
CREATE UNIQUE INDEX IF NOT EXISTS uq_drafts_email_user
    ON drafts (email_id, user_id)
    WHERE email_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drafts_user_email
    ON drafts (user_id, email_id);

CREATE INDEX IF NOT EXISTS idx_drafts_user_status
    ON drafts (user_id, status);


-- ============================================================
-- FOLLOW-UPS
-- Emails that require a follow-up if no reply arrives.
-- ============================================================

CREATE TABLE IF NOT EXISTS follow_ups (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email_id       TEXT,
    to_email       TEXT,
    subject        TEXT,
    topic          TEXT,
    sent_at        TIMESTAMPTZ,
    follow_up_by   TIMESTAMPTZ,
    status         TEXT        NOT NULL DEFAULT 'waiting',  -- waiting | replied | followed_up | closed
    urgency        TEXT,
    auto_draft     TEXT,        -- pre-written follow-up ready to approve + send
    reminder_count INT         NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE follow_ups ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own follow ups"
    ON follow_ups FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_follow_ups_user_status
    ON follow_ups (user_id, status, follow_up_by);


-- ============================================================
-- CONTACTS
-- Relationship intelligence profiles. Primary key is (email, user_id)
-- so two Felix users can have separate profiles for the same contact.
-- ============================================================

CREATE TABLE IF NOT EXISTS contacts (
    email                  TEXT    NOT NULL,
    user_id                UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name                   TEXT,
    company                TEXT,
    role                   TEXT,
    -- Relationship signals
    vip                    BOOLEAN NOT NULL DEFAULT FALSE,
    vip_rules              JSONB,
    relationship_strength  FLOAT,      -- 0.0 → 1.0, computed weekly
    total_emails           INT     NOT NULL DEFAULT 0,
    last_contacted         TIMESTAMPTZ,
    meeting_count          INT     NOT NULL DEFAULT 0,
    last_meeting           TIMESTAMPTZ,
    -- Context
    topics_discussed       TEXT[]  NOT NULL DEFAULT '{}',
    open_commitments       TEXT[]  NOT NULL DEFAULT '{}',   -- things you've promised them
    their_open_commitments TEXT[]  NOT NULL DEFAULT '{}',   -- things they've promised you
    sentiment_trend        TEXT,       -- improving | stable | deteriorating
    known_facts            JSONB,      -- {"assistant": "Jane", "timezone": "EST"}
    personal_notes         TEXT,
    tags                   TEXT[]  NOT NULL DEFAULT '{}',   -- client | investor | advisor
    style_profile          JSONB,      -- their communication style
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (email, user_id)
);

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own contacts"
    ON contacts FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_contacts_user_vip
    ON contacts (user_id, vip);

CREATE INDEX IF NOT EXISTS idx_contacts_user_strength
    ON contacts (user_id, relationship_strength DESC);


-- ============================================================
-- MEETINGS
-- Meeting records with AI-generated notes, action items, decisions.
-- ============================================================

CREATE TABLE IF NOT EXISTS meetings (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    calendar_event_id   TEXT,
    title               TEXT,
    date                TIMESTAMPTZ,
    duration_minutes    INT,
    attendees           TEXT[]      NOT NULL DEFAULT '{}',
    transcript          TEXT,
    summary             TEXT,
    action_items        JSONB,      -- [{item, owner, deadline}]
    decisions           JSONB,
    open_questions      JSONB,
    follow_up_email_id  TEXT,       -- Gmail message ID of the sent follow-up email
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own meetings"
    ON meetings FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_meetings_user_date
    ON meetings (user_id, date DESC);


-- ============================================================
-- DAILY BRIEFINGS
-- Generated each morning at the user's configured briefing_time.
-- UNIQUE(user_id, date) ensures only one briefing per day per user.
-- ============================================================

CREATE TABLE IF NOT EXISTS briefings (
    id                  UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID  NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    date                DATE  NOT NULL,
    text                TEXT,           -- spoken briefing script
    audio_url           TEXT,           -- Supabase Storage URL for ElevenLabs audio
    priority_emails     JSONB,          -- snapshot of priority emails included
    calendar_summary    JSONB,          -- snapshot of today's meetings
    follow_ups_summary  JSONB,          -- snapshot of overdue follow-ups
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    listened_at         TIMESTAMPTZ,    -- set when user plays the audio
    UNIQUE (user_id, date)
);

ALTER TABLE briefings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own briefings"
    ON briefings FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_briefings_user_date
    ON briefings (user_id, date DESC);


-- ============================================================
-- VOICE SESSIONS
-- Log of every voice interaction for debugging + context replay.
-- ============================================================

CREATE TABLE IF NOT EXISTS voice_sessions (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    transcript   TEXT,
    intent       JSONB,      -- parsed intent from Claude Haiku
    response     TEXT,       -- Felix's text response
    action_taken TEXT,       -- e.g. "sent_email", "scheduled_meeting"
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE voice_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own voice sessions"
    ON voice_sessions FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_voice_sessions_user_created
    ON voice_sessions (user_id, created_at DESC);
