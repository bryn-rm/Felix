-- ============================================================
-- Migration 018 — digest_sends: per-slot dedup marker for digest emails
-- ============================================================
-- check_digest_mode previously relied on interval spacing (one run per
-- 30-min slot) for idempotency. A redeploy mid-slot resets the interval
-- anchor: run at 08:05 (slot 08:00, digest sent) → deploy → new process
-- runs at 08:25 (slot 08:00 again) → second digest. The scheduler now
-- claims a (user_id, slot_date, slot_time) row with INSERT ... ON CONFLICT
-- DO NOTHING before sending; only the claimant sends.
--
-- slot_date/slot_time are in the user's local timezone (matches the slot
-- comparison in scheduler._maybe_send_digest). Rows older than 14 days are
-- pruned by the hourly sweep job.
--
-- Apply via Supabase: Dashboard → SQL Editor → New query → paste this file.
-- All statements are idempotent (IF NOT EXISTS guards).
-- ============================================================

CREATE TABLE IF NOT EXISTS digest_sends (
    user_id    UUID  NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    slot_date  DATE  NOT NULL,               -- user-local date of the slot
    slot_time  TEXT  NOT NULL,               -- "HH:MM" rounded to the 30-min slot
    sent_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, slot_date, slot_time)
);

-- Backend-only bookkeeping (service role bypasses RLS). Enable RLS with no
-- policies so the anon/authenticated paths can't touch it at all.
ALTER TABLE digest_sends ENABLE ROW LEVEL SECURITY;
