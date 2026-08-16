-- ============================================================
-- Migration 020 — Explicit meeting type and interview role
-- ============================================================
-- New browser-capture meetings always write an explicit meeting_type. Existing
-- rows intentionally remain NULL: that is the compatibility marker used by
-- Live Assist to retain the prior template-based interview behaviour.
-- ============================================================

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS meeting_type TEXT,
    ADD COLUMN IF NOT EXISTS user_role    TEXT;

COMMENT ON COLUMN meetings.meeting_type IS
    'Explicit Live Assist mode: general|interview; NULL identifies a legacy meeting';
COMMENT ON COLUMN meetings.user_role IS
    'User role for an explicit interview: candidate|interviewer';

-- Drop first so re-running this migration also repairs the earlier draft of
-- the constraint. PostgreSQL considers a CHECK satisfied when its expression
-- is NULL, hence the outer IS TRUE rather than relying on the OR branches.
ALTER TABLE meetings DROP CONSTRAINT IF EXISTS meetings_type_role_chk;
ALTER TABLE meetings ADD CONSTRAINT meetings_type_role_chk CHECK (
    (
        (meeting_type IS NULL AND user_role IS NULL)
        OR (meeting_type = 'general' AND user_role IS NULL)
        OR (
            meeting_type = 'interview'
            AND user_role IN ('candidate', 'interviewer')
        )
    ) IS TRUE
);
