-- Forward-fix pending_calendar_proposals after it changed from one row per
-- user (user_id primary key) to multiple rows per user (id primary key).

ALTER TABLE pending_calendar_proposals
    ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();

UPDATE pending_calendar_proposals
SET id = gen_random_uuid()
WHERE id IS NULL;

ALTER TABLE pending_calendar_proposals
    ALTER COLUMN id SET NOT NULL;

DO $$
DECLARE
    pk_name text;
BEGIN
    SELECT conname INTO pk_name
    FROM pg_constraint
    WHERE conrelid = 'pending_calendar_proposals'::regclass
      AND contype = 'p';

    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pending_calendar_proposals DROP CONSTRAINT %I', pk_name);
    END IF;
END$$;

ALTER TABLE pending_calendar_proposals
    ADD PRIMARY KEY (id);

CREATE INDEX IF NOT EXISTS idx_pending_calendar_proposals_user_created
    ON pending_calendar_proposals(user_id, created_at DESC);
