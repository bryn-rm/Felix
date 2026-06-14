-- ============================================================
-- Migration 014 — Email search indexes
-- ============================================================
-- Adds trigram indexes for free-text email search across the local inbound
-- and sent-mail mirrors. Gmail remains the source of truth; these indexes
-- make Felix's cached search more tolerant and faster.
--
-- The indexed expressions below MUST stay byte-for-byte identical to
-- _INBOUND_HAYSTACK / _SENT_HAYSTACK in backend/app/services/chat_tools.py —
-- the GIN pg_trgm index is only used when the query's positive `LIKE` operand
-- matches the indexed expression exactly.
--
-- Index expressions must be IMMUTABLE. CONCAT_WS and ARRAY_TO_STRING are only
-- STABLE (they depend on type-output functions), so we build the haystacks from
-- IMMUTABLE pieces instead: `COALESCE() || ' ' || ...` for scalars, and a small
-- IMMUTABLE wrapper (felix_array_text) for the text[] recipient columns.
--
-- On an already-populated database, build these with CREATE INDEX CONCURRENTLY
-- (run each statement outside a transaction block, e.g. in the Supabase SQL
-- editor) to avoid holding a write lock on emails/sent_emails during the build.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- IMMUTABLE array→text join. array_to_string is STABLE and Postgres ships no
-- immutable builtin for this, but joining a text[] with a constant delimiter is
-- genuinely immutable (text output never varies), so wrapping it is safe.
CREATE OR REPLACE FUNCTION felix_array_text(text[])
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
    AS $$ SELECT COALESCE(array_to_string($1, ' '), '') $$;

CREATE INDEX IF NOT EXISTS idx_emails_search_trgm
    ON emails USING GIN (
        (LOWER(COALESCE(from_name, '') || ' ' || COALESCE(from_email, '') || ' ' ||
               COALESCE(to_email, '') || ' ' || COALESCE(subject, '') || ' ' ||
               COALESCE(snippet, '') || ' ' || COALESCE(body, '')))
        gin_trgm_ops
    );

CREATE INDEX IF NOT EXISTS idx_sent_emails_search_trgm
    ON sent_emails USING GIN (
        (LOWER(COALESCE(from_email, '') || ' ' || felix_array_text(to_emails) || ' ' ||
               felix_array_text(to_names) || ' ' || COALESCE(subject, '') || ' ' ||
               COALESCE(snippet, '') || ' ' || COALESCE(body, '')))
        gin_trgm_ops
    );
