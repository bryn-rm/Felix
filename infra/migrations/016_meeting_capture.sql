-- ============================================================
-- Migration 016 — Meeting Capture (Granola-style, browser capture)
-- ============================================================
-- Adds:
--   • settings.meeting_capture_mode  — per-user gate flag (fails closed: off by
--                                      default, nav item + /meetings hidden, the
--                                      capture WS + REST routes reject)
--   • meetings.<new columns>         — capture lifecycle state on the EXISTING
--                                      meetings table (calendar_event_id, title,
--                                      attendees, date already exist — reused, not
--                                      re-added)
--   • meeting_transcript_segments    — one row per FINALIZED STT result (interims
--                                      live on the WS only and are discarded)
--   • meeting_summaries              — enhanced AI output, versionable
--   • commitments.source_meeting_id  — link meeting-sourced action items back to
--                                      the meeting; source_kind CHECK widened to
--                                      allow 'meeting'
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS /
-- duplicate_object guards) so the file can be re-run safely.
-- ============================================================


-- ── settings.meeting_capture_mode ────────────────────────────────────────────
-- Fails closed: off/unset hides the nav item + /meetings surface and makes the
-- capture WS + REST routes reject (per the fail-closed-on-unset-config convention).

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS meeting_capture_mode BOOLEAN NOT NULL DEFAULT FALSE;


-- ============================================================
-- MEETINGS — extend the existing table for browser capture
-- calendar_event_id, title, attendees, date already exist (see schema.sql) and
-- are reused. The legacy transcript/summary/action_items/decisions/open_questions
-- columns belong to the voice/generate_meeting_notes path and are left untouched;
-- the capture path writes meeting_transcript_segments + meeting_summaries instead.
-- ============================================================

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS template   TEXT,                         -- general|one_on_one|interview|sales|standup|user_research
    ADD COLUMN IF NOT EXISTS status     TEXT NOT NULL DEFAULT 'idle', -- idle|recording|processing|done|error
    ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'browser_capture',
    ADD COLUMN IF NOT EXISTS user_notes TEXT,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ended_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- status CHECK as a named, idempotent constraint (re-runnable).
DO $$ BEGIN
    ALTER TABLE meetings ADD CONSTRAINT meetings_status_chk
        CHECK (status IN ('idle','recording','processing','done','error'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_meetings_user_status
    ON meetings (user_id, status);

CREATE INDEX IF NOT EXISTS idx_meetings_user_started
    ON meetings (user_id, started_at DESC);

-- Partial index backing the auto-end sweep (scheduler queries status='recording').
CREATE INDEX IF NOT EXISTS idx_meetings_status_recording
    ON meetings (status) WHERE status = 'recording';


-- ============================================================
-- MEETING TRANSCRIPT SEGMENTS (Feature: Meeting Capture)
-- One row per FINALIZED STT result. Only finals persist; interims stream over the
-- WS for display and are discarded. ts_start is meeting-relative seconds (computed
-- from bytes consumed, so it stays correct across the per-channel STT stream
-- rollover) — never wall-clock.
-- ============================================================

CREATE TABLE IF NOT EXISTS meeting_transcript_segments (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    meeting_id  UUID        NOT NULL REFERENCES meetings(id)   ON DELETE CASCADE,
    speaker     TEXT        NOT NULL,            -- me | them
    text        TEXT        NOT NULL,
    ts_start    FLOAT       NOT NULL,            -- seconds from meeting start (rollover-safe)
    ts_end      FLOAT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (speaker IN ('me','them'))
);

ALTER TABLE meeting_transcript_segments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own meeting segments" ON meeting_transcript_segments;
CREATE POLICY "users manage own meeting segments"
    ON meeting_transcript_segments FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_meeting_segments_meeting
    ON meeting_transcript_segments (user_id, meeting_id, ts_start);


-- ============================================================
-- MEETING SUMMARIES (Feature: Meeting Capture — enhanced AI output)
-- Versionable: one row per summarize run (latest by created_at). enhanced_notes
-- preserves the user's original notes verbatim as origin:'user' blocks, with
-- origin:'ai' blocks added only for context/structure.
-- ============================================================

CREATE TABLE IF NOT EXISTS meeting_summaries (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    meeting_id     UUID        NOT NULL REFERENCES meetings(id)   ON DELETE CASCADE,
    tldr           TEXT,
    decisions      JSONB       NOT NULL DEFAULT '[]',  -- [{text}]
    action_items   JSONB       NOT NULL DEFAULT '[]',  -- [{text, owner, due_hint}]
    enhanced_notes JSONB       NOT NULL DEFAULT '[]',  -- [{origin:'user'|'ai', text}]
    model          TEXT,
    confidence     FLOAT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE meeting_summaries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own meeting summaries" ON meeting_summaries;
CREATE POLICY "users manage own meeting summaries"
    ON meeting_summaries FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_meeting_summaries_meeting
    ON meeting_summaries (user_id, meeting_id, created_at DESC);


-- ============================================================
-- COMMITMENTS — allow meeting-sourced action items
-- The action-item → commitment fan-out (commitment_service.create_from_meeting)
-- inserts rows with source_kind='meeting'. The original 008 CHECK only permits
-- ('inbound','sent'), so it must be widened.
-- ============================================================

ALTER TABLE commitments
    ADD COLUMN IF NOT EXISTS source_meeting_id UUID REFERENCES meetings(id) ON DELETE SET NULL;

-- The 008 source_kind CHECK is an unnamed inline constraint; Postgres auto-named
-- it `commitments_source_kind_check` (confirmed against the live DB, 2026-06-25).
-- Drop that and re-add a stably-named constraint that also allows 'meeting'.
DO $$ BEGIN
    ALTER TABLE commitments DROP CONSTRAINT IF EXISTS commitments_source_kind_check;
EXCEPTION WHEN others THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE commitments ADD CONSTRAINT commitments_source_kind_chk
        CHECK (source_kind IN ('inbound','sent','meeting'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
