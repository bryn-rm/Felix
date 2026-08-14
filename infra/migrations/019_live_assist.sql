-- ============================================================
-- Migration 019 — Live In-Meeting Assistant
-- ============================================================
-- Adds:
--   • settings.live_assist_mode   — per-user opt-in flag for the live assist
--                                   sidebar. Fails closed: off/unset means no
--                                   watcher is started, ask frames are refused,
--                                   the assist REST routes 404, and the sidebar
--                                   never renders. Effective only when
--                                   meeting_capture_mode is also enabled.
--   • meetings.live_context       — prefetched context digest written at meeting
--                                   start (best-effort spawn); a pre-meeting
--                                   snapshot, not live state.
--   • meeting_assist_items        — one row per assist card (proactive or ask).
--                                   Carries evaluation metadata (usefulness_score,
--                                   trigger_type, prompt_version, metadata) so the
--                                   feature can be measured without further
--                                   migrations.
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS) so the
-- file can be re-run safely.
-- ============================================================


-- ── settings.live_assist_mode ────────────────────────────────────────────────
-- Separate from meeting_capture_mode: enabling capture must not silently start
-- spending AI budget on live inference. Off by default, fails closed.

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS live_assist_mode BOOLEAN NOT NULL DEFAULT FALSE;


-- ── meetings.live_context ────────────────────────────────────────────────────
-- Compact digest (attendee context, open commitments, recent threads, memory
-- episodes) + keyword match-list for the deterministic candidate gate. Written
-- by live_assist_service.prefetch_context via spawn() at meeting start.

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS live_context JSONB;


-- ============================================================
-- MEETING ASSIST ITEMS (Feature: Live In-Meeting Assistant)
-- One row per card surfaced during a meeting — proactive (salience-gated watch
-- pipeline) or ask (user-typed question). Persistence is the dedupe seed across
-- reconnects, the reconnect replay source, post-meeting review, and the
-- evaluation dataset. usefulness_score is the model's self-assessed ranking
-- signal (uncalibrated — not a probability). metadata is the instrumentation
-- escape hatch ({window_segments, latency_ms, speaker_trigger, dedupe_reason, …}).
-- ============================================================

CREATE TABLE IF NOT EXISTS meeting_assist_items (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    meeting_id       UUID        NOT NULL REFERENCES meetings(id)   ON DELETE CASCADE,
    kind             TEXT        NOT NULL,            -- context|answer|fact|contradiction|follow_up
    source           TEXT        NOT NULL,            -- proactive | ask
    question         TEXT,                            -- ask items only
    title            TEXT        NOT NULL,
    body             TEXT        NOT NULL,
    transcript_ts    FLOAT,                           -- meeting-relative seconds anchor
    dismissed        BOOLEAN     NOT NULL DEFAULT FALSE,
    usefulness_score FLOAT,                           -- model self-score (uncalibrated)
    trigger_type     TEXT,                            -- which gate path fired (proactive only)
    prompt_version   TEXT,
    request_id       TEXT,                            -- client-supplied id (ask only)
    metadata         JSONB       NOT NULL DEFAULT '{}',
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (kind IN ('context','answer','fact','contradiction','follow_up')),
    CHECK (source IN ('proactive','ask'))
);

ALTER TABLE meeting_assist_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users manage own assist items" ON meeting_assist_items;
CREATE POLICY "users manage own assist items"
    ON meeting_assist_items FOR ALL
    USING (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_meeting_assist_items_meeting
    ON meeting_assist_items (user_id, meeting_id, created_at);
