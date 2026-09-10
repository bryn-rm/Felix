-- On-demand discovery only. No source backfill or project activity triggers.
BEGIN;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS discovery_token UUID;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS discovery_started_at TIMESTAMPTZ;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS discovery_request_id UUID;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS discovery_completed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS project_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('email_thread', 'meeting', 'commitment')),
    source_id TEXT NOT NULL CHECK (length(source_id) BETWEEN 1 AND 500),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'accepted', 'dismissed', 'invalid')),
    explanation TEXT NOT NULL DEFAULT '' CHECK (length(explanation) <= 300),
    score DOUBLE PRECISION CHECK (score BETWEEN 0 AND 1),
    context_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    candidate_manifest JSONB NOT NULL DEFAULT '[]'::jsonb,
    model TEXT,
    prompt_version TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE,
    UNIQUE (user_id, project_id, kind, source_id)
);
-- Polymorphic IDs are proposals, never authority. The service resolves them
-- through the owned canonical catalog on every read and through Phase 1 on add.
CREATE INDEX IF NOT EXISTS project_suggestions_pending
    ON project_suggestions (user_id, project_id, state, generated_at DESC);
ALTER TABLE project_suggestions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_suggestions_owner ON project_suggestions;
CREATE POLICY project_suggestions_owner ON project_suggestions FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
-- API-only storage: cached prose requires live source/context revalidation.
DO $$ DECLARE role_name TEXT; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON project_suggestions FROM %I', role_name);
        END IF;
    END LOOP;
END $$;
COMMIT;
