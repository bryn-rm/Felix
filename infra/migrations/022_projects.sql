-- Phase 1 Project Hubs. No backfill or provider/model calls.
-- Apply after 021. Transactional and safe to rerun. The two supporting unique
-- indexes briefly lock meetings/commitments while they are built; schedule this
-- migration appropriately for a large installation.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS meetings_user_id_id_key ON meetings (user_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS commitments_user_id_id_key ON commitments (user_id, id);

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 10000),
    target_date DATE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, id)
);
CREATE INDEX IF NOT EXISTS projects_user_status_updated ON projects (user_id, status, updated_at DESC);
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS projects_owner ON projects;
CREATE POLICY projects_owner ON projects FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- Gmail has message mirrors but no thread parent. Register only explicitly
-- selected, locally mirrored threads. This identity survives message deletion;
-- availability and content are always read from the mirrors, never copied here.
CREATE TABLE IF NOT EXISTS project_email_threads (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL CHECK (length(thread_id) BETWEEN 1 AND 500),
    PRIMARY KEY (user_id, thread_id)
);
ALTER TABLE project_email_threads ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_email_threads_owner ON project_email_threads;
CREATE POLICY project_email_threads_owner ON project_email_threads FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid() AND (
        EXISTS (SELECT 1 FROM emails e WHERE e.user_id = project_email_threads.user_id AND e.thread_id = project_email_threads.thread_id)
        OR EXISTS (SELECT 1 FROM sent_emails e WHERE e.user_id = project_email_threads.user_id AND e.thread_id = project_email_threads.thread_id)
    ));

CREATE TABLE IF NOT EXISTS project_thread_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    thread_id TEXT NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, thread_id) REFERENCES project_email_threads(user_id, thread_id) ON DELETE CASCADE,
    UNIQUE (user_id, project_id, thread_id)
);
CREATE INDEX IF NOT EXISTS project_thread_links_source ON project_thread_links (user_id, thread_id);
ALTER TABLE project_thread_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_thread_links_owner ON project_thread_links;
CREATE POLICY project_thread_links_owner ON project_thread_links FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE TABLE IF NOT EXISTS project_meeting_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    meeting_id UUID,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE,
    -- Keep the association as an unavailable placeholder when a source is
    -- deleted. PostgreSQL 15+ supports setting only the source column to null.
    FOREIGN KEY (user_id, meeting_id) REFERENCES meetings(user_id, id) ON DELETE SET NULL (meeting_id),
    UNIQUE (user_id, project_id, meeting_id)
);
CREATE INDEX IF NOT EXISTS project_meeting_links_source ON project_meeting_links (user_id, meeting_id);
ALTER TABLE project_meeting_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_meeting_links_owner ON project_meeting_links;
CREATE POLICY project_meeting_links_owner ON project_meeting_links FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE TABLE IF NOT EXISTS project_commitment_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    commitment_id UUID,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, commitment_id) REFERENCES commitments(user_id, id) ON DELETE SET NULL (commitment_id),
    UNIQUE (user_id, project_id, commitment_id)
);
CREATE INDEX IF NOT EXISTS project_commitment_links_source ON project_commitment_links (user_id, commitment_id);
ALTER TABLE project_commitment_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_commitment_links_owner ON project_commitment_links;
CREATE POLICY project_commitment_links_owner ON project_commitment_links FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE TABLE IF NOT EXISTS project_activity (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('created', 'renamed', 'description_changed', 'target_date_changed', 'archived', 'reopened', 'linked', 'unlinked')),
    source_kind TEXT CHECK (source_kind IN ('email_thread', 'meeting', 'commitment')),
    details JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS project_activity_user_project_time ON project_activity (user_id, project_id, occurred_at DESC, id);
ALTER TABLE project_activity ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_activity_owner ON project_activity;
CREATE POLICY project_activity_owner ON project_activity FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- Audit in the same transaction as the mutation. ON CONFLICT DO NOTHING does
-- not fire an INSERT trigger, so concurrent/retried links log exactly once.
CREATE OR REPLACE FUNCTION project_log_changes() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
DECLARE field_name TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO project_activity (user_id, project_id, action)
        VALUES (NEW.user_id, NEW.id, 'created');
    ELSE
        FOREACH field_name IN ARRAY ARRAY['name', 'description', 'target_date', 'status'] LOOP
            IF to_jsonb(NEW)->field_name IS DISTINCT FROM to_jsonb(OLD)->field_name THEN
                INSERT INTO project_activity (user_id, project_id, action, details)
                VALUES (NEW.user_id, NEW.id,
                    CASE field_name WHEN 'name' THEN 'renamed' WHEN 'description' THEN 'description_changed'
                    WHEN 'target_date' THEN 'target_date_changed'
                    ELSE CASE WHEN NEW.status = 'archived' THEN 'archived' ELSE 'reopened' END END,
                    CASE WHEN field_name = 'description' THEN '{}'::jsonb
                    ELSE jsonb_build_object('before', to_jsonb(OLD)->field_name, 'after', to_jsonb(NEW)->field_name) END);
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS project_changes ON projects;
CREATE TRIGGER project_changes AFTER INSERT OR UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION project_log_changes();

CREATE OR REPLACE FUNCTION project_log_link() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
DECLARE item RECORD;
BEGIN
    IF TG_OP = 'INSERT' THEN item := NEW; ELSE item := OLD; END IF;
    INSERT INTO project_activity (user_id, project_id, action, source_kind, details)
    SELECT item.user_id, item.project_id,
           CASE WHEN TG_OP = 'INSERT' THEN 'linked' ELSE 'unlinked' END, TG_ARGV[0],
           jsonb_build_object('source_id', to_jsonb(item)->TG_ARGV[1])
    WHERE EXISTS (SELECT 1 FROM projects p WHERE p.user_id = item.user_id AND p.id = item.project_id);
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS project_thread_link_changes ON project_thread_links;
CREATE TRIGGER project_thread_link_changes AFTER INSERT OR DELETE ON project_thread_links
    FOR EACH ROW EXECUTE FUNCTION project_log_link('email_thread', 'thread_id');
DROP TRIGGER IF EXISTS project_meeting_link_changes ON project_meeting_links;
CREATE TRIGGER project_meeting_link_changes AFTER INSERT OR DELETE ON project_meeting_links
    FOR EACH ROW EXECUTE FUNCTION project_log_link('meeting', 'meeting_id');
DROP TRIGGER IF EXISTS project_commitment_link_changes ON project_commitment_links;
CREATE TRIGGER project_commitment_link_changes AFTER INSERT OR DELETE ON project_commitment_links
    FOR EACH ROW EXECUTE FUNCTION project_log_link('commitment', 'commitment_id');
COMMIT;
