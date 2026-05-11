-- Migration 012 — Email read/archived state
-- Mirrors Gmail UNREAD / INBOX label state locally so the inbox UI can react
-- immediately to mark-read and archive actions without waiting for the next
-- inbox_sync pass. PATCH /emails/{id} keeps these in lockstep with Gmail.
-- Run after 011_template_category.sql.

ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS read BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE;

-- Inbox list query: WHERE user_id = $1 AND archived = FALSE ORDER BY received_at DESC.
CREATE INDEX IF NOT EXISTS idx_emails_user_archived_received
    ON emails (user_id, archived, received_at DESC);
