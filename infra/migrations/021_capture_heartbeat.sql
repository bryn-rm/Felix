-- ============================================================
-- Migration 021 — Shared capture-socket liveness
-- ============================================================
-- The viewer may be served by a different backend process from the capture
-- WebSocket, so process-local watcher state cannot describe attachment.
-- A per-connection token prevents an older socket from clearing a newer
-- connection's heartbeat during reconnect/takeover.
-- ============================================================

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS capture_connection_id UUID,
    ADD COLUMN IF NOT EXISTS capture_heartbeat_at  TIMESTAMPTZ;

COMMENT ON COLUMN meetings.capture_connection_id IS
    'Token for the currently attached capture WebSocket; guards reconnect cleanup';
COMMENT ON COLUMN meetings.capture_heartbeat_at IS
    'Last database heartbeat from the attached capture WebSocket';
