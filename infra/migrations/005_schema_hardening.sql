-- ============================================================
-- Migration 005 — Schema hardening (REVIEW_4 fixes)
-- ============================================================
-- Adds:
--   • NOT NULL constraints on critical email fields
--   • Foreign key constraints from drafts/follow_ups to emails
--   • CHECK constraints for category/urgency/status enums
--   • Missing index on (user_id, thread_id)
--   • RLS on eval_runs table
--   • Tightened ai_feedback RLS (INSERT + SELECT only; no DELETE/UPDATE)
--   • NOT NULL + ON DELETE CASCADE on ai_calls.user_id
-- ============================================================


-- ── emails: enforce NOT NULL on core fields ──────────────────────────────────
-- Use DEFAULT '' so existing NULL rows are converted gracefully.

ALTER TABLE emails
    ALTER COLUMN from_email SET DEFAULT '',
    ALTER COLUMN subject     SET DEFAULT '',
    ALTER COLUMN body        SET DEFAULT '';

UPDATE emails SET from_email = '' WHERE from_email IS NULL;
UPDATE emails SET subject     = '' WHERE subject IS NULL;
UPDATE emails SET body        = '' WHERE body IS NULL;

ALTER TABLE emails
    ALTER COLUMN from_email SET NOT NULL,
    ALTER COLUMN subject     SET NOT NULL,
    ALTER COLUMN body        SET NOT NULL;


-- ── emails: CHECK constraints for triage enums ───────────────────────────────

ALTER TABLE emails
    ADD CONSTRAINT ck_emails_category CHECK (
        category IS NULL OR category IN (
            'action_required', 'fyi', 'waiting_on', 'newsletter', 'automated', 'vip'
        )
    ),
    ADD CONSTRAINT ck_emails_urgency CHECK (
        urgency IS NULL OR urgency IN ('low', 'medium', 'high', 'critical')
    );


-- ── drafts: FK to emails + CHECK on status ───────────────────────────────────

ALTER TABLE drafts
    ADD CONSTRAINT fk_drafts_email
        FOREIGN KEY (email_id, user_id) REFERENCES emails(id, user_id) ON DELETE CASCADE,
    ADD CONSTRAINT ck_drafts_status CHECK (
        status IN ('pending', 'approved', 'sent', 'discarded')
    );


-- ── follow_ups: FK to emails + CHECK on status ───────────────────────────────

ALTER TABLE follow_ups
    ADD CONSTRAINT fk_follow_ups_email
        FOREIGN KEY (email_id, user_id) REFERENCES emails(id, user_id) ON DELETE CASCADE,
    ADD CONSTRAINT ck_follow_ups_status CHECK (
        status IN ('waiting', 'replied', 'followed_up', 'closed')
    );


-- ── emails: index for thread look-ups ────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_emails_user_thread
    ON emails (user_id, thread_id);


-- ── ai_calls: NOT NULL + cascade on user_id ──────────────────────────────────
-- Existing NULL rows must be deleted (or reassigned) before applying NOT NULL.

DELETE FROM ai_calls WHERE user_id IS NULL;

ALTER TABLE ai_calls
    ALTER COLUMN user_id SET NOT NULL,
    DROP CONSTRAINT IF EXISTS ai_calls_user_id_fkey;

ALTER TABLE ai_calls
    ADD CONSTRAINT ai_calls_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


-- ── eval_runs: enable RLS (service-role only) ────────────────────────────────

ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service role only on eval_runs"
    ON eval_runs FOR ALL
    USING (auth.role() = 'service_role');


-- ── meetings: add storage for AI-generated follow-up email draft ─────────────
-- The meeting_notes prompt returns follow_up_email_subject + follow_up_email_body
-- but the table only had follow_up_email_id (the Gmail message ID after sending).
-- Add columns so the draft can be persisted before the user sends it.

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS follow_up_email_subject TEXT,
    ADD COLUMN IF NOT EXISTS follow_up_email_body    TEXT;


-- ── ai_feedback: tighten RLS — INSERT + SELECT only (no DELETE/UPDATE) ───────

DROP POLICY IF EXISTS "users manage own feedback" ON ai_feedback;

CREATE POLICY "users insert own feedback"
    ON ai_feedback FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "users view own feedback"
    ON ai_feedback FOR SELECT
    USING (user_id = auth.uid());
