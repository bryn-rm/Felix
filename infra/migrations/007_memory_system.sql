-- ============================================================
-- Migration 007 — Three-layer memory system
-- ============================================================
-- Adds:
--   • user_memory           — Layer 1 slow-changing user profile / preferences
--   • session_summaries     — Layer 2 cross-session continuity
--   • memory_episodes       — Layer 3 long-term episodic memory (with embeddings)
--   • memory_operations     — observability log for memory retrievals
--
-- pgvector is required for semantic retrieval. If the extension is not
-- available the embedding column falls back to a nullable text column and
-- retrieval degrades to recency + entity matching only.
-- ============================================================


-- ── pgvector ─────────────────────────────────────────────────────────────────

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable; memory embeddings will fall back to text storage';
END $$;


-- ── Layer 1 · user_memory ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_memory (
    user_id     UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    profile     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    preferences JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE user_memory ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own memory"
    ON user_memory FOR ALL
    USING (user_id = auth.uid());


-- ── Layer 2 · session_summaries ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS session_summaries (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    summary          TEXT        NOT NULL,
    open_items       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    session_metadata JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_user_created
    ON session_summaries (user_id, created_at DESC);

ALTER TABLE session_summaries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own session summaries"
    ON session_summaries FOR ALL
    USING (user_id = auth.uid());


-- ── Layer 3 · memory_episodes ────────────────────────────────────────────────

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        EXECUTE '
            CREATE TABLE IF NOT EXISTS memory_episodes (
                id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                episode_type TEXT        NOT NULL,
                summary      TEXT        NOT NULL,
                entities     JSONB       NOT NULL DEFAULT ''[]''::jsonb,
                importance   FLOAT       NOT NULL DEFAULT 0.5,
                source_type  TEXT,
                source_id    TEXT,
                occurred_at  TIMESTAMPTZ NOT NULL,
                embedding    vector(1536),
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_memory_episodes_type CHECK (
                    episode_type IN (''email'', ''meeting'', ''chat'', ''commitment'', ''decision'', ''summary'')
                ),
                CONSTRAINT ck_memory_episodes_importance CHECK (importance >= 0 AND importance <= 1)
            )
        ';

        EXECUTE '
            CREATE INDEX IF NOT EXISTS idx_memory_episodes_embedding
                ON memory_episodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
        ';
    ELSE
        EXECUTE '
            CREATE TABLE IF NOT EXISTS memory_episodes (
                id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                episode_type TEXT        NOT NULL,
                summary      TEXT        NOT NULL,
                entities     JSONB       NOT NULL DEFAULT ''[]''::jsonb,
                importance   FLOAT       NOT NULL DEFAULT 0.5,
                source_type  TEXT,
                source_id    TEXT,
                occurred_at  TIMESTAMPTZ NOT NULL,
                embedding    TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_memory_episodes_type CHECK (
                    episode_type IN (''email'', ''meeting'', ''chat'', ''commitment'', ''decision'', ''summary'')
                ),
                CONSTRAINT ck_memory_episodes_importance CHECK (importance >= 0 AND importance <= 1)
            )
        ';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_memory_episodes_user_occurred
    ON memory_episodes (user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_episodes_entities
    ON memory_episodes USING gin (entities);

CREATE INDEX IF NOT EXISTS idx_memory_episodes_user_type
    ON memory_episodes (user_id, episode_type);

ALTER TABLE memory_episodes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users manage own episodes"
    ON memory_episodes FOR ALL
    USING (user_id = auth.uid());


-- ── Memory operations log (service-role only) ────────────────────────────────

CREATE TABLE IF NOT EXISTS memory_operations (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    operation      TEXT        NOT NULL,        -- retrieve | distil | consolidate | prune
    feature        TEXT,                        -- draft | chat | briefing | …
    episodes_hit   INT,
    latency_ms     INT,
    tokens_used    INT,
    metadata       JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_ops_user_created
    ON memory_operations (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_ops_operation
    ON memory_operations (operation, created_at DESC);

ALTER TABLE memory_operations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service role only on memory_operations"
    ON memory_operations FOR ALL
    USING (auth.role() = 'service_role');
