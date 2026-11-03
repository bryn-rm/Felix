-- Keep revoked/expired grants distinguishable from working Google connections.
-- Existing rows remain eligible until Google rejects their refresh token.
-- Metadata-only default on supported PostgreSQL versions; takes a brief
-- ACCESS EXCLUSIVE lock. Existing backend-only RLS and user_id PK are unchanged.
ALTER TABLE google_connections
    ADD COLUMN IF NOT EXISTS needs_reconnect BOOLEAN NOT NULL DEFAULT FALSE;
