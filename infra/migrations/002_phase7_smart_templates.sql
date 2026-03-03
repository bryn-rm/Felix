-- Migration 002 — Phase 7
-- Smart template library: per-user email templates, not shared between users.
-- Run in Supabase SQL Editor after 001_phase2_email_fields.sql.

-- Required for GIN index on a mixed (uuid, text[]) column set
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE TABLE IF NOT EXISTS smart_templates (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name           TEXT        NOT NULL,           -- e.g. "Meeting follow-up", "Intro email"
    subject_template TEXT      NOT NULL DEFAULT '', -- may include {{placeholders}}
    body_template  TEXT        NOT NULL,
    tags           TEXT[]      NOT NULL DEFAULT '{}',  -- for quick filtering
    use_count      INT         NOT NULL DEFAULT 0,  -- incremented on each use
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE smart_templates ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own templates"
    ON smart_templates FOR ALL
    USING (user_id = auth.uid());

-- Quick lookup by user + tag
CREATE INDEX IF NOT EXISTS idx_smart_templates_user_tag
    ON smart_templates USING GIN (user_id, tags);

CREATE INDEX IF NOT EXISTS idx_smart_templates_user_created
    ON smart_templates (user_id, created_at DESC);
