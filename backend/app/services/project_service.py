"""Manual project workspaces. Sources remain canonical; no provider/AI calls."""

from uuid import UUID

from fastapi import HTTPException

from app import db

PROJECT_FIELDS = "id, user_id, name, description, target_date, status, created_at, updated_at"


# Identifiers are an internal allowlist, never interpolated from request text.
LINKS = {
    "email_thread": ("project_thread_links", "thread_id"),
    "meeting": ("project_meeting_links", "meeting_id"),
    "commitment": ("project_commitment_links", "commitment_id"),
}

# The same tenant-filtered catalog powers search and source rendering. Thread
# identity is Gmail thread_id across both mirrors, not an arbitrary message id.
CATALOG_TEMPLATE = """
WITH messages AS (
    SELECT user_id, thread_id, id, subject, from_email AS participant,
           received_at AS occurred_at, 'inbound' AS direction
    FROM emails WHERE user_id = $1 AND NULLIF(thread_id, '') IS NOT NULL {thread_scope}
    UNION ALL
    SELECT user_id, thread_id, id, subject, array_to_string(to_emails, ', '),
           sent_at, 'sent'
    FROM sent_emails WHERE user_id = $1 AND NULLIF(thread_id, '') IS NOT NULL {thread_scope}
), threads AS (
    SELECT DISTINCT ON (thread_id) * FROM messages
    ORDER BY thread_id, occurred_at DESC NULLS LAST, id
), catalog AS (
    SELECT 'email_thread' AS kind, thread_id AS source_id,
           COALESCE(NULLIF(subject, ''), '(no subject)') AS title,
           participant AS detail, occurred_at, NULL::text AS status,
           NULL::timestamptz AS deadline, NULL::text AS href
    FROM threads WHERE user_id = $1
    UNION ALL
    SELECT 'meeting', id::text, COALESCE(NULLIF(title, ''), 'Untitled meeting'),
           array_to_string(attendees, ', '), COALESCE(started_at, date, created_at),
           status, NULL::timestamptz,
           CASE WHEN status = 'recording' THEN '/meetings/live/' ELSE '/meetings/' END || id::text
    FROM meetings WHERE user_id = $1 {meeting_scope}
      AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)
    UNION ALL
    SELECT 'commitment', id::text, text, COALESCE(counterparty_name, counterparty_email),
           created_at, status, deadline, '/commitments?direction=all&status=' || status || '#commitment-' || id::text
    FROM commitments WHERE user_id = $1 {commitment_scope}
)
"""


def project_catalog(*, include_history=False):
    """Restrict source work before grouping mail into thread identities."""
    scopes = {}
    for kind, (table, column) in LINKS.items():
        field = "thread_id" if kind == "email_thread" else "id"
        values = f"SELECT {column} FROM {table} WHERE user_id = $1 AND project_id = $2"
        if include_history:
            # Historical link actions may still display the owned source title.
            # Compare as text; JSON activity is not a trusted UUID input.
            values = f"SELECT {column}::text FROM {table} WHERE user_id = $1 AND project_id = $2 UNION SELECT details->>'source_id' FROM project_activity WHERE user_id = $1 AND project_id = $2 AND source_kind = '{kind}'"
            field += "::text"
        scopes[{"email_thread": "thread", "meeting": "meeting", "commitment": "commitment"}[kind] + "_scope"] = f"AND {field} IN ({values})"
    return CATALOG_TEMPLATE.format(**scopes)


# PostgreSQL can inline this single-use CTE and prune unrelated UNION branches
# for the search kind predicate. Detail/activity explicitly scope it by project.
CATALOG = CATALOG_TEMPLATE.format(thread_scope="", meeting_scope="", commitment_scope="")
PROJECT_CATALOG = project_catalog()
ACTIVITY_CATALOG = project_catalog(include_history=True)

ASSOCIATIONS = """
    SELECT id, user_id, project_id, 'email_thread' AS kind, thread_id AS source_id, linked_at
    FROM project_thread_links WHERE user_id = $1 AND project_id = $2
    UNION ALL
    SELECT id, user_id, project_id, 'meeting', meeting_id::text, linked_at
    FROM project_meeting_links WHERE user_id = $1 AND project_id = $2
    UNION ALL
    SELECT id, user_id, project_id, 'commitment', commitment_id::text, linked_at
    FROM project_commitment_links WHERE user_id = $1 AND project_id = $2
"""


def source_key(kind: str, value: str):
    if kind not in LINKS:
        raise HTTPException(422, "Unsupported source type")
    if kind == "email_thread":
        if not value.strip() or len(value) > 500:
            raise HTTPException(422, "Invalid thread id")
        return value
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(422, "Invalid source id") from None


class ProjectService:
    async def get(self, user_id, project_id):
        row = await db.query_one(
            f"SELECT {PROJECT_FIELDS} FROM projects WHERE user_id = $1 AND id = $2", user_id, project_id,
        )
        if not row:
            raise HTTPException(404, "Project not found")
        return row

    async def list(self, user_id, status="active", limit=50, offset=0):
        return await db.query(
            f"SELECT {PROJECT_FIELDS} FROM projects WHERE user_id = $1 AND status = $2 "
            "ORDER BY updated_at DESC, id LIMIT $3 OFFSET $4",
            user_id, status, limit, offset,
        )

    async def create(self, user_id, values):
        row = await db.insert("projects", {"user_id": user_id, **values})
        return {key: row[key] for key in PROJECT_FIELDS.split(", ")}

    async def edit(self, user_id, project_id, values):
        # Only the API's validated fields are accepted; null clears target_date.
        allowed = {"name", "description", "target_date", "status"}
        patch = {key: value for key, value in values.items() if key in allowed}
        if not patch:
            return await self.get(user_id, project_id)
        assignments = [f"{key} = ${index}" for index, key in enumerate(patch, 3)]
        row = await db.query_one(
            "UPDATE projects SET " + ", ".join(assignments) + ", updated_at = now() "
            f"WHERE user_id = $1 AND id = $2 RETURNING {PROJECT_FIELDS}",
            user_id, project_id, *patch.values(),
        )
        if not row:
            raise HTTPException(404, "Project not found")
        return row

    async def search(self, user_id, kind, search="", limit=30, offset=0):
        # Literal substring search: '%' and '_' entered by the user aren't SQL wildcards.
        term = "%" + search.replace("^", "^^").replace("%", "^%").replace("_", "^_") + "%"
        return await db.query(
            CATALOG + "SELECT * FROM catalog WHERE kind = $2 "
            "AND (title ILIKE $3 ESCAPE '^' OR detail ILIKE $3 ESCAPE '^') "
            "ORDER BY occurred_at DESC NULLS LAST, source_id LIMIT $4 OFFSET $5",
            user_id, kind, term, limit, offset,
        )

    async def link(self, user_id, project_id, kind, source_id):
        # Reject malformed identities before acquiring a database connection.
        source_key(kind, source_id)
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            return await self.link_in_transaction(conn, user_id, project_id, kind, source_id)

    async def link_in_transaction(self, conn, user_id, project_id, kind, source_id):
        """Canonical link path, also used by atomic suggestion acceptance."""
        key = source_key(kind, source_id)
        table, column = LINKS[kind]
        # Serialize link/unlink per project, including concurrent retries.
        project = await conn.fetchrow(
            "SELECT id FROM projects WHERE user_id = $1 AND id = $2 FOR UPDATE",
            user_id, project_id,
        )
        if not project:
            raise HTTPException(404, "Project not found")
        if kind == "email_thread":
            owned = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM emails WHERE user_id = $1 AND thread_id = $2) "
                "OR EXISTS (SELECT 1 FROM sent_emails WHERE user_id = $1 AND thread_id = $2)",
                user_id, key,
            )
            if owned:
                await conn.execute(
                    "INSERT INTO project_email_threads (user_id, thread_id) VALUES ($1, $2) "
                    "ON CONFLICT (user_id, thread_id) DO NOTHING", user_id, key,
                )
        else:
            source_table = "meetings" if kind == "meeting" else "commitments"
            gate = (
                " AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)"
                if kind == "meeting" else ""
            )
            owned = await conn.fetchval(
                f"SELECT id FROM {source_table} WHERE user_id = $1 AND id = $2" + gate + " FOR KEY SHARE",
                user_id, key,
            )
        if not owned:
            raise HTTPException(404, "Source unavailable")
        row = await conn.fetchrow(
            f"INSERT INTO {table} (user_id, project_id, {column}) VALUES ($1, $2, $3) "
            f"ON CONFLICT (user_id, project_id, {column}) DO NOTHING RETURNING id",
            user_id, project_id, key,
        )
        if not row:
            row = await conn.fetchrow(
                f"SELECT id FROM {table} WHERE user_id = $1 AND project_id = $2 AND {column} = $3",
                user_id, project_id, key,
            )
        return {"id": row["id"], "kind": kind}

    async def unlink(self, user_id, project_id, kind, link_id):
        table, _ = LINKS[kind]
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            project = await conn.fetchrow(
                "SELECT id FROM projects WHERE user_id = $1 AND id = $2 FOR UPDATE", user_id, project_id,
            )
            if not project:
                raise HTTPException(404, "Project not found")
            await conn.execute(
                f"DELETE FROM {table} WHERE user_id = $1 AND project_id = $2 AND id = $3",
                user_id, project_id, link_id,
            )

    async def detail(self, user_id, project_id):
        project = await self.get(user_id, project_id)
        sources = await db.query(
            PROJECT_CATALOG + ", associations AS (" + ASSOCIATIONS + ") "
            "SELECT a.id, a.kind, a.source_id, a.linked_at, c.title, c.detail, c.occurred_at, "
            "c.status, c.deadline, c.href, (c.source_id IS NOT NULL) AS available "
            "FROM associations a LEFT JOIN catalog c ON c.kind = a.kind AND c.source_id = a.source_id "
            "WHERE a.user_id = $1 AND a.project_id = $2 ORDER BY a.linked_at DESC, a.id",
            user_id, project_id,
        )
        for source in sources:
            if not source["available"]:
                source["title"] = "Source unavailable"
                source["unavailable_reason"] = (
                    "Deleted or meeting access is disabled." if source["kind"] == "meeting"
                    else "This source is no longer stored in Felix."
                )
        return {
            "project": project, "sources": sources,
            "source_count": len(sources),
            "open_commitment_count": sum(s["kind"] == "commitment" and s["status"] == "open" for s in sources),
        }

    async def activity(self, user_id, project_id, limit=50, offset=0):
        await self.get(user_id, project_id)
        # Source occurrences are derived from currently linked canonical rows.
        # Lifecycle/link events are durable, even after a source is unlinked.
        return await db.query(
            ACTIVITY_CATALOG + """
            , events AS (
                SELECT a.id::text, a.action, a.source_kind,
                       a.details || CASE WHEN a.source_kind IS NULL THEN '{}'::jsonb
                                    ELSE jsonb_build_object('title', c.title) END AS details,
                       a.occurred_at, 'project' AS event_type
                FROM project_activity a LEFT JOIN catalog c
                  ON c.kind = a.source_kind AND c.source_id = a.details->>'source_id'
                WHERE a.user_id = $1 AND a.project_id = $2
                UNION ALL
                SELECT 'email-' || e.id, 'email_received', 'email_thread', jsonb_build_object('title', e.subject), e.received_at, 'source'
                FROM emails e JOIN project_thread_links l ON l.user_id = e.user_id AND l.thread_id = e.thread_id
                WHERE e.user_id = $1 AND l.user_id = $1 AND l.project_id = $2
                UNION ALL
                SELECT 'sent-' || e.id, 'email_sent', 'email_thread', jsonb_build_object('title', e.subject), e.sent_at, 'source'
                FROM sent_emails e JOIN project_thread_links l ON l.user_id = e.user_id AND l.thread_id = e.thread_id
                WHERE e.user_id = $1 AND l.user_id = $1 AND l.project_id = $2
                UNION ALL
                SELECT 'meeting-' || m.id, 'meeting_occurred', 'meeting', jsonb_build_object('title', m.title), COALESCE(m.started_at, m.date), 'source'
                FROM meetings m JOIN project_meeting_links l ON l.user_id = m.user_id AND l.meeting_id = m.id
                WHERE m.user_id = $1 AND l.user_id = $1 AND l.project_id = $2
                  AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)
                UNION ALL
                SELECT 'commitment-' || c.id, 'commitment_captured', 'commitment', jsonb_build_object('title', c.text), c.created_at, 'source'
                FROM commitments c JOIN project_commitment_links l ON l.user_id = c.user_id AND l.commitment_id = c.id
                WHERE c.user_id = $1 AND l.user_id = $1 AND l.project_id = $2
                UNION ALL
                SELECT 'resolved-' || c.id, 'commitment_resolved', 'commitment', jsonb_build_object('title', c.text, 'status', c.status), c.resolved_at, 'source'
                FROM commitments c JOIN project_commitment_links l ON l.user_id = c.user_id AND l.commitment_id = c.id
                WHERE c.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 AND c.resolved_at IS NOT NULL
            )
            SELECT * FROM events WHERE occurred_at IS NOT NULL
            ORDER BY occurred_at DESC, id LIMIT $3 OFFSET $4
            """, user_id, project_id, limit, offset,
        )

    async def thread(self, user_id, project_id, thread_id):
        await self.get(user_id, project_id)
        linked = await db.query_one(
            "SELECT id FROM project_thread_links WHERE user_id = $1 AND project_id = $2 AND thread_id = $3",
            user_id, project_id, thread_id,
        )
        if not linked:
            raise HTTPException(404, "Source unavailable")
        return await db.query(
            "SELECT id, subject, from_email AS participant, body, received_at AS occurred_at, 'inbound' AS direction "
            "FROM emails WHERE user_id = $1 AND thread_id = $2 UNION ALL "
            "SELECT id, subject, array_to_string(to_emails, ', '), body, sent_at, 'sent' "
            "FROM sent_emails WHERE user_id = $1 AND thread_id = $2 "
            "ORDER BY occurred_at DESC NULLS LAST, id LIMIT 100", user_id, thread_id,
        )


project_service = ProjectService()
