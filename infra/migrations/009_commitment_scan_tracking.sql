-- ============================================================
-- Migration 009 — Durable commitment-scan tracking
-- ============================================================
-- Adds a per-row scan-status timestamp to `emails` and `sent_emails`. The
-- inbox-sync pipeline previously fired commitment extraction as an untracked
-- background task *after* labeling the message `felix-processed`, so a
-- transient Anthropic / DB failure during extraction silently dropped the
-- commitment with no chance of retry (the label excludes the row from future
-- syncs).
--
-- With this column, scans that fail leave the timestamp NULL; the next sync
-- run sweeps unscanned rows from the last 7 days and retries.
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent.
-- ============================================================


-- ── Per-row scan status ─────────────────────────────────────────────────────

ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS commitment_scanned_at TIMESTAMPTZ;

ALTER TABLE sent_emails
    ADD COLUMN IF NOT EXISTS commitment_scanned_at TIMESTAMPTZ;


-- ── Partial indexes for the catch-up sweep ─────────────────────────────────
-- The sweep query is `WHERE commitment_scanned_at IS NULL ORDER BY ...`,
-- so a partial index keeps it cheap even as the tables grow.

CREATE INDEX IF NOT EXISTS idx_emails_user_unscanned
    ON emails (user_id, received_at DESC)
    WHERE commitment_scanned_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_sent_emails_user_unscanned
    ON sent_emails (user_id, sent_at DESC)
    WHERE commitment_scanned_at IS NULL;


-- ── Optional: clear stale meeting_preps generated under the old shell-wrap
-- ── behavior (Fix 3 in the same patch). Cards regenerate on the next 5-min
-- ── scheduler tick, with body-only HTML cached.
-- Uncomment and run once if you have already populated `meeting_preps` rows
-- whose content_html starts with `<!doctype html>`:
--
--   DELETE FROM meeting_preps WHERE content_html LIKE '<!doctype html>%';


-- ── Sent-mail recipient arrays (review fix) ────────────────────────────────
-- Migration 008 originally stored a single to_email/to_name per sent message,
-- which collapsed group threads (To: a@x, b@y, c@z) to the first recipient.
-- Migration 008 has been edited in place to use TEXT[] columns; this block
-- migrates any existing rows. Idempotent — safe to re-run.
--
-- If you have already applied 008 with the old columns, run this once via
-- Supabase: Dashboard → SQL Editor → New query.

ALTER TABLE sent_emails
    ADD COLUMN IF NOT EXISTS to_emails TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS to_names  TEXT[] NOT NULL DEFAULT '{}';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sent_emails' AND column_name = 'to_email'
    ) THEN
        UPDATE sent_emails
        SET to_emails = ARRAY[to_email]
        WHERE to_email IS NOT NULL
          AND to_email <> ''
          AND (to_emails IS NULL OR cardinality(to_emails) = 0);
        UPDATE sent_emails
        SET to_names = ARRAY[to_name]
        WHERE to_name IS NOT NULL
          AND (to_names IS NULL OR cardinality(to_names) = 0);
        ALTER TABLE sent_emails DROP COLUMN to_email;
        ALTER TABLE sent_emails DROP COLUMN to_name;
    END IF;
END$$;
