-- Migration 003: oauth_nonces table
-- Stores one-time CSRF nonces for the Google OAuth connect flow.
-- Keyed on the nonce itself so a user can have multiple concurrent in-flight
-- attempts (a second /connect call does not invalidate the first).
-- Nonces expire after 10 minutes; a scheduled job sweeps expired rows.

CREATE TABLE IF NOT EXISTS oauth_nonces (
    nonce       TEXT        NOT NULL PRIMARY KEY,
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS oauth_nonces_user_id_idx    ON oauth_nonces (user_id);
CREATE INDEX IF NOT EXISTS oauth_nonces_expires_at_idx ON oauth_nonces (expires_at);

-- RLS: users can only see/delete their own nonce rows.
-- The backend uses the service key which bypasses RLS, but this defends
-- against any accidental exposure via the anon/authenticated keys.
ALTER TABLE oauth_nonces ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their own nonce"
    ON oauth_nonces
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
