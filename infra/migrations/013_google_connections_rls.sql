-- Migration 013 — Lock down google_connections RLS
-- The previous policy ("users manage own google connection") let any
-- authenticated browser session read its own encrypted access_token /
-- refresh_token ciphertext via the Supabase anon/authenticated keys. Encryption
-- defends against DB leaks, but ciphertext should never reach the browser at
-- all. The backend (postgres role, BYPASSRLS) is unaffected. Frontend reads
-- connection status through GET /auth/google/status only.

DROP POLICY IF EXISTS "users manage own google connection"
    ON google_connections;

DROP POLICY IF EXISTS "service role manages google connections"
    ON google_connections;

CREATE POLICY "service role manages google connections"
    ON google_connections FOR ALL
    USING (auth.role() = 'service_role');
