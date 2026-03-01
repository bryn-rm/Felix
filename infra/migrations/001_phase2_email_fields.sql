-- Migration 001 — Phase 2
-- Adds fields required for proper reply threading and draft uniqueness.
-- Run in Supabase SQL Editor after the initial schema.sql.

-- Store the RFC 2822 Message-ID header so replies can set In-Reply-To correctly.
ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS message_id_header TEXT;

-- Ensure only one draft per (email, user) — makes upserts safe.
-- Drop first in case of re-run.
DROP INDEX IF EXISTS uq_drafts_email_user;
CREATE UNIQUE INDEX uq_drafts_email_user ON drafts (email_id, user_id)
    WHERE email_id IS NOT NULL;
