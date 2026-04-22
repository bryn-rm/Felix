-- 006: Security hardening — admin audit log table

CREATE TABLE IF NOT EXISTS admin_audit (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id),
    email       TEXT        NOT NULL,
    endpoint    TEXT        NOT NULL,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_accessed
    ON admin_audit (accessed_at DESC);

-- RLS: backend writes via the postgres role (BYPASSRLS); no client should
-- ever read/write this table with the anon or authenticated key.
ALTER TABLE admin_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_audit FORCE ROW LEVEL SECURITY;
