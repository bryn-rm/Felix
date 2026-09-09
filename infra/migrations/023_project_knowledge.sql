-- Phase 2: confirmed project knowledge and separately stored derived updates.
-- Forward-only after 022; rerunnable. No data backfill or model calls. Building
-- the supporting summary index takes a lock on meeting_summaries.
BEGIN;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS update_generation_token UUID;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS update_generation_started_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS meeting_summaries_user_id_meeting_key
    ON meeting_summaries (user_id, id, meeting_id);
CREATE UNIQUE INDEX IF NOT EXISTS meeting_summaries_user_id_id_key
    ON meeting_summaries (user_id, id);

CREATE TABLE IF NOT EXISTS project_scope_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    content TEXT NOT NULL CHECK (length(content) <= 10000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE,
    UNIQUE (user_id, project_id, version)
);
ALTER TABLE project_scope_versions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_scope_owner_read ON project_scope_versions;
CREATE POLICY project_scope_owner_read ON project_scope_versions FOR SELECT USING (user_id = auth.uid());
DROP POLICY IF EXISTS project_scope_owner_insert ON project_scope_versions;
CREATE POLICY project_scope_owner_insert ON project_scope_versions FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE TABLE IF NOT EXISTS project_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('decision', 'approval', 'milestone')),
    title TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 500),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 3000),
    owner TEXT NOT NULL DEFAULT '' CHECK (length(owner) <= 200),
    status TEXT NOT NULL,
    event_date DATE,
    deadline DATE,
    supersedes_id UUID,
    creation_key TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE,
    UNIQUE (user_id, project_id, id),
    UNIQUE (user_id, project_id, creation_key),
    FOREIGN KEY (user_id, project_id, supersedes_id) REFERENCES project_records(user_id, project_id, id),
    UNIQUE (user_id, project_id, supersedes_id),
    CHECK (supersedes_id IS NULL OR (kind = 'decision' AND supersedes_id <> id)),
    CHECK ((kind = 'decision' AND status IN ('current', 'superseded') AND event_date IS NOT NULL AND deadline IS NULL AND owner = '')
        OR (kind = 'approval' AND status IN ('pending', 'approved', 'declined', 'cancelled') AND event_date IS NULL AND supersedes_id IS NULL)
        OR (kind = 'milestone' AND status IN ('planned', 'done', 'cancelled') AND event_date IS NOT NULL AND deadline IS NULL AND owner = '' AND supersedes_id IS NULL))
);
CREATE INDEX IF NOT EXISTS project_records_user_project_kind ON project_records (user_id, project_id, kind, created_at DESC);
ALTER TABLE project_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_records_owner ON project_records;
CREATE POLICY project_records_owner ON project_records FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- Store every accepted request, including a second tab's request for an already
-- recorded decision. A key cannot later be reused with different input.
CREATE TABLE IF NOT EXISTS project_record_requests (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    request_id UUID NOT NULL,
    request_hash TEXT NOT NULL,
    record_id UUID NOT NULL,
    PRIMARY KEY (user_id, project_id, request_id),
    FOREIGN KEY (user_id, project_id, record_id) REFERENCES project_records(user_id, project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS project_record_requests_record ON project_record_requests (user_id, project_id, record_id);
ALTER TABLE project_record_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_record_requests_owner ON project_record_requests;
CREATE POLICY project_record_requests_owner ON project_record_requests FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- Typed canonical references. Unlinking a project source does not rewrite a
-- decision's evidence. The API resolves availability against current project
-- associations and gates. Nullable references preserve deleted-source markers.
CREATE TABLE IF NOT EXISTS project_record_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    record_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('email_thread', 'meeting', 'commitment')),
    thread_id TEXT,
    meeting_id UUID,
    commitment_id UUID,
    summary_id UUID,
    decision_index INTEGER CHECK (decision_index >= 0),
    decision_hash TEXT,
    FOREIGN KEY (user_id, project_id, record_id) REFERENCES project_records(user_id, project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, thread_id) REFERENCES project_email_threads(user_id, thread_id) ON DELETE SET NULL (thread_id),
    FOREIGN KEY (user_id, meeting_id) REFERENCES meetings(user_id, id) ON DELETE SET NULL (meeting_id),
    FOREIGN KEY (user_id, commitment_id) REFERENCES commitments(user_id, id) ON DELETE SET NULL (commitment_id),
    FOREIGN KEY (user_id, summary_id, meeting_id) REFERENCES meeting_summaries(user_id, id, meeting_id) ON DELETE SET NULL (summary_id),
    -- Independent of meeting_id: meeting deletion can null that column before
    -- summary deletion, causing MATCH SIMPLE to skip the three-column FK.
    FOREIGN KEY (user_id, summary_id) REFERENCES meeting_summaries(user_id, id) ON DELETE SET NULL (summary_id),
    CHECK ((kind = 'email_thread' AND meeting_id IS NULL AND commitment_id IS NULL)
        OR (kind = 'meeting' AND thread_id IS NULL AND commitment_id IS NULL)
        OR (kind = 'commitment' AND thread_id IS NULL AND meeting_id IS NULL)),
    CHECK ((decision_index IS NULL AND decision_hash IS NULL AND summary_id IS NULL)
        OR (kind = 'meeting' AND decision_index IS NOT NULL AND length(decision_hash) = 64))
);
CREATE INDEX IF NOT EXISTS project_record_evidence_record ON project_record_evidence (user_id, project_id, record_id);
CREATE INDEX IF NOT EXISTS project_record_evidence_meeting ON project_record_evidence (user_id, meeting_id);
CREATE INDEX IF NOT EXISTS project_record_evidence_summary ON project_record_evidence (user_id, summary_id, meeting_id);
CREATE INDEX IF NOT EXISTS project_record_evidence_thread ON project_record_evidence (user_id, thread_id);
CREATE INDEX IF NOT EXISTS project_record_evidence_commitment ON project_record_evidence (user_id, commitment_id);
ALTER TABLE project_record_evidence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_record_evidence_owner ON project_record_evidence;
CREATE POLICY project_record_evidence_owner ON project_record_evidence FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE TABLE IF NOT EXISTS project_updates (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    request_id UUID NOT NULL,
    claims JSONB NOT NULL,
    evidence_manifest JSONB NOT NULL,
    fingerprint TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    week_start TIMESTAMPTZ NOT NULL,
    week_end TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id),
    FOREIGN KEY (user_id, project_id) REFERENCES projects(user_id, id) ON DELETE CASCADE
);
ALTER TABLE project_updates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_updates_owner ON project_updates;
CREATE POLICY project_updates_owner ON project_updates FOR ALL
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- Derived/imported text must pass API access revalidation even for its owner.
-- Supabase defaults can grant public-schema tables to browser roles: explicitly
-- remove that bypass. Owner RLS policies remain defence in depth.
DO $$ DECLARE role_name TEXT; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON project_records, project_record_requests, project_record_evidence, project_updates FROM %I', role_name);
        END IF;
    END LOOP;
END $$;

ALTER TABLE project_activity DROP CONSTRAINT IF EXISTS project_activity_action_check;
ALTER TABLE project_activity ADD CONSTRAINT project_activity_action_check CHECK (action IN (
    'created', 'renamed', 'description_changed', 'target_date_changed', 'archived', 'reopened', 'linked', 'unlinked',
    'scope_changed', 'record_created', 'record_changed', 'commitment_changed'
));

CREATE OR REPLACE FUNCTION project_knowledge_activity() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    INSERT INTO project_activity (user_id, project_id, action, details)
    VALUES (NEW.user_id, NEW.project_id,
        CASE WHEN TG_TABLE_NAME = 'project_scope_versions' THEN 'scope_changed'
             WHEN TG_OP = 'INSERT' THEN 'record_created' ELSE 'record_changed' END,
        jsonb_build_object('record_id', NEW.id, 'record_kind',
            CASE WHEN TG_TABLE_NAME = 'project_scope_versions' THEN 'scope' ELSE to_jsonb(NEW)->>'kind' END,
            'version', NEW.version));
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS project_scope_activity ON project_scope_versions;
CREATE TRIGGER project_scope_activity AFTER INSERT ON project_scope_versions
    FOR EACH ROW EXECUTE FUNCTION project_knowledge_activity();
DROP TRIGGER IF EXISTS project_record_activity ON project_records;
CREATE TRIGGER project_record_activity AFTER INSERT OR UPDATE ON project_records
    FOR EACH ROW EXECUTE FUNCTION project_knowledge_activity();

CREATE OR REPLACE FUNCTION project_record_version() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF OLD.kind = 'decision' AND (
        (to_jsonb(NEW) - ARRAY['status','version','updated_at']) IS DISTINCT FROM
        (to_jsonb(OLD) - ARRAY['status','version','updated_at'])
        OR NOT (OLD.status = 'current' AND NEW.status = 'superseded')) THEN
        RAISE EXCEPTION 'Decisions are immutable; create a replacement' USING ERRCODE = '23514';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['version','updated_at']) IS NOT DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['version','updated_at']) THEN RETURN NULL; END IF;
    NEW.version := OLD.version + 1;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS project_record_version_change ON project_records;
CREATE TRIGGER project_record_version_change BEFORE UPDATE ON project_records
    FOR EACH ROW EXECUTE FUNCTION project_record_version();

-- Canonical commitments lacked a general modification timestamp. Record only
-- that a material change occurred, without maintaining a project-owned copy.
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS project_changed_at TIMESTAMPTZ;
CREATE OR REPLACE FUNCTION project_commitment_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF (NEW.text, NEW.status, NEW.deadline) IS DISTINCT FROM (OLD.text, OLD.status, OLD.deadline) THEN
        NEW.project_changed_at := clock_timestamp();
        INSERT INTO project_activity (user_id, project_id, action, source_kind, details, occurred_at)
        SELECT NEW.user_id, l.project_id, 'commitment_changed', 'commitment',
               jsonb_build_object('source_id', NEW.id), NEW.project_changed_at
        FROM project_commitment_links l WHERE l.user_id = NEW.user_id AND l.commitment_id = NEW.id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS project_commitment_changed ON commitments;
CREATE TRIGGER project_commitment_changed BEFORE UPDATE ON commitments
    FOR EACH ROW EXECUTE FUNCTION project_commitment_change();
COMMIT;
