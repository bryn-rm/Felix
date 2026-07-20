-- ============================================================
-- Migration 017 — drafts: allow 'sending' status
-- ============================================================
-- POST /emails/{id}/send now claims the draft atomically before calling
-- Gmail (UPDATE ... SET status = 'sending' WHERE status NOT IN
-- ('sent','sending') RETURNING *) so a double-click or client retry can't
-- interleave two requests that both read status='pending' and both send.
-- The 005 CHECK constraint only allowed pending|approved|sent|discarded,
-- so it must be recreated with 'sending' included.
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent (DROP CONSTRAINT IF EXISTS guard).
-- ============================================================

ALTER TABLE drafts
    DROP CONSTRAINT IF EXISTS ck_drafts_status;

ALTER TABLE drafts
    ADD CONSTRAINT ck_drafts_status CHECK (
        status IN ('pending', 'approved', 'sending', 'sent', 'discarded')
    );

-- A draft stuck in 'sending' (process died mid-send) blocks future sends;
-- the API releases the claim on every failure path, so any lingering row
-- predates this migration or a crash. Reset any strays defensively.
UPDATE drafts SET status = 'pending' WHERE status = 'sending';
