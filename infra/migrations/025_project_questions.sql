-- Latest project question/answer, separately from confirmed project knowledge.
-- Rerunnable; no backfill. ALTER TABLE briefly locks projects.
BEGIN;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS question_generation_token UUID;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS question_generation_started_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS project_answers (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    request_id UUID NOT NULL,
    question TEXT NOT NULL CHECK (length(btrim(question)) BETWEEN 1 AND 2000),
    answer JSONB NOT NULL,
    evidence_manifest JSONB NOT NULL,
    fingerprint TEXT NOT NULL,
    omitted_count INTEGER NOT NULL CHECK (omitted_count >= 0),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, project_id),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE
);
ALTER TABLE project_answers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_answers_owner ON project_answers;
CREATE POLICY project_answers_owner ON project_answers FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
-- Generated prose may depend on any input evidence. Only the API may read it,
-- after rechecking project ownership, links, source availability and gates.
DO $$ DECLARE role_name TEXT; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON project_answers FROM %I', role_name);
        END IF;
    END LOOP;
END $$;
COMMIT;
