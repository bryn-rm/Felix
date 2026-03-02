-- Migration 003: oauth_nonces table
-- Stores one-time CSRF nonces for the Google OAuth connect flow.
-- Each user can have at most one pending nonce (ON CONFLICT DO UPDATE in app code).
-- Nonces expire after 10 minutes; expired rows can be purged by a periodic job.

CREATE TABLE IF NOT EXISTS oauth_nonces (
    user_id     UUID        NOT NULL PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    nonce       TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for fast expiry scans (optional cleanup job)
CREATE INDEX IF NOT EXISTS oauth_nonces_expires_at_idx ON oauth_nonces (expires_at);

-- RLS: users can only see/delete their own nonce row.
-- The backend uses the service key which bypasses RLS, but this defends
-- against any accidental exposure via the anon/authenticated keys.
ALTER TABLE oauth_nonces ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their own nonce"
    ON oauth_nonces
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
