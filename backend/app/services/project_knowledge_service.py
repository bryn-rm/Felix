"""User-confirmed project records and reference-only supporting evidence."""

import hashlib
import json
from uuid import uuid4
from fastapi import HTTPException

from app import db
from app.services.project_service import LINKS, project_service, source_key


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


async def lock_project(conn, user_id, project_id):
    if not await conn.fetchval(
        "SELECT id FROM projects WHERE user_id = $1 AND id = $2 FOR UPDATE", user_id, project_id,
    ):
        raise HTTPException(404, "Project not found")


class ProjectKnowledgeService:
    async def scope(self, user_id, project_id, content, expected_version):
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await lock_project(conn, user_id, project_id)
            current = await conn.fetchrow(
                "SELECT * FROM project_scope_versions WHERE user_id = $1 AND project_id = $2 ORDER BY version DESC LIMIT 1",
                user_id, project_id,
            )
            if (current["version"] if current else 0) != expected_version:
                raise HTTPException(409, "Scope changed. Reload before saving.")
            if current and current["content"] == content:
                return dict(current)
            return dict(await conn.fetchrow(
                "INSERT INTO project_scope_versions (user_id, project_id, version, content) VALUES ($1, $2, $3, $4) RETURNING *",
                user_id, project_id, expected_version + 1, content,
            ))

    async def _evidence(self, conn, user_id, project_id, record_id, sources):
        seen = set()
        for source in sources:
            kind = source["kind"]
            key = source_key(kind, str(source["source_id"]))
            if (kind, key) in seen:
                continue
            seen.add((kind, key))
            table, column = LINKS[kind]
            gate = " AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)" if kind == "meeting" else ""
            if not await conn.fetchval(
                f"SELECT id FROM {table} WHERE user_id = $1 AND project_id = $2 AND {column} = $3" + gate,
                user_id, project_id, key,
            ):
                raise HTTPException(404, "Supporting source is not available in this project")
            if kind == "email_thread" and not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM emails WHERE user_id = $1 AND thread_id = $2) "
                "OR EXISTS (SELECT 1 FROM sent_emails WHERE user_id = $1 AND thread_id = $2)", user_id, key,
            ):
                raise HTTPException(404, "Supporting source unavailable")
            await conn.execute(
                f"INSERT INTO project_record_evidence (user_id, project_id, record_id, kind, {column}, summary_id, decision_index, decision_hash) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                user_id, project_id, record_id, kind, key, source.get("summary_id"),
                source.get("decision_index"), source.get("decision_hash"),
            )

    async def create(self, user_id, project_id, values, *, import_summary=None):
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await lock_project(conn, user_id, project_id)
            values = dict(values)
            request_id = values.pop("request_id", None) or uuid4()
            request_hash = text_hash(json.dumps([values, import_summary], sort_keys=True, default=str))
            replay = await conn.fetchrow(
                "SELECT record_id, request_hash FROM project_record_requests WHERE user_id = $1 AND project_id = $2 AND request_id = $3",
                user_id, project_id, request_id,
            )
            if replay:
                if replay["request_hash"] != request_hash:
                    raise HTTPException(409, "This request ID was already used with different input")
                return {"id": replay["record_id"]}
            evidence = values.pop("evidence", [])
            creation_key = None
            if values["kind"] == "decision":
                creation_key = (f"summary:{import_summary[0]}:{import_summary[1]}" if import_summary else
                                text_hash(json.dumps([values, sorted(evidence, key=lambda e: (e["kind"], str(e["source_id"])))], sort_keys=True, default=str)))
            if import_summary:
                summary_id, index = import_summary
                summary = await conn.fetchrow(
                    "SELECT s.* FROM meeting_summaries s JOIN project_meeting_links l "
                    "ON l.user_id = s.user_id AND l.meeting_id = s.meeting_id "
                    "WHERE s.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 AND s.id = $3 "
                    "AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)",
                    user_id, project_id, summary_id,
                )
                decisions = summary["decisions"] if summary else []
                if not isinstance(decisions, list) or not 0 <= index < len(decisions):
                    raise HTTPException(404, "Meeting decision unavailable")
                item = decisions[index]
                statement = item.get("text") if isinstance(item, dict) else None
                if not isinstance(statement, str) or not statement.strip() or len(statement) > 500:
                    raise HTTPException(422, "Meeting decision cannot be imported; enter a concise decision manually")
                values["title"] = statement
                evidence = [{"kind": "meeting", "source_id": summary["meeting_id"],
                             "summary_id": summary_id, "decision_index": index, "decision_hash": text_hash(statement)}]
            if creation_key:
                existing = await conn.fetchval(
                    "SELECT id FROM project_records WHERE user_id = $1 AND project_id = $2 AND creation_key = $3",
                    user_id, project_id, creation_key,
                )
                if existing:
                    await self._remember_request(conn, user_id, project_id, request_id, request_hash, existing)
                    return {"id": existing}
            kind = values["kind"]
            supersedes = values.get("supersedes_id")
            if supersedes:
                old = await conn.fetchrow(
                    "SELECT * FROM project_records WHERE user_id = $1 AND project_id = $2 AND id = $3 FOR UPDATE",
                    user_id, project_id, supersedes,
                )
                if not old:
                    raise HTTPException(404, "Decision not found")
                if kind != "decision" or old["kind"] != "decision" or old["status"] != "current":
                    raise HTTPException(409, "Only a current decision can be replaced")
                await conn.execute(
                    "UPDATE project_records SET status = 'superseded' WHERE user_id = $1 AND project_id = $2 AND id = $3",
                    user_id, project_id, supersedes,
                )
            status = {"decision": "current", "approval": "pending", "milestone": "planned"}[kind]
            row = await conn.fetchrow(
                "INSERT INTO project_records (user_id, project_id, kind, title, description, owner, status, event_date, deadline, supersedes_id, creation_key) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *",
                user_id, project_id, kind, values["title"], values.get("description", ""), values.get("owner", ""),
                status, values.get("event_date"), values.get("deadline"), supersedes, creation_key,
            )
            await self._evidence(conn, user_id, project_id, row["id"], evidence)
            await self._remember_request(conn, user_id, project_id, request_id, request_hash, row["id"])
            return {"id": row["id"]}

    async def _remember_request(self, conn, user_id, project_id, request_id, request_hash, record_id):
        await conn.execute(
            "INSERT INTO project_record_requests (user_id,project_id,request_id,request_hash,record_id) VALUES ($1,$2,$3,$4,$5)",
            user_id, project_id, request_id, request_hash, record_id,
        )

    async def edit(self, user_id, project_id, record_id, values, expected_version):
        pool = await db.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await lock_project(conn, user_id, project_id)
            old = await conn.fetchrow(
                "SELECT * FROM project_records WHERE user_id = $1 AND project_id = $2 AND id = $3 FOR UPDATE",
                user_id, project_id, record_id,
            )
            if not old:
                raise HTTPException(404, "Record not found")
            if old["kind"] == "decision":
                raise HTTPException(409, "Preserve this decision by creating a replacement")
            if old["version"] != expected_version:
                raise HTTPException(409, "Record changed. Reload before saving.")
            allowed = {"title", "description", "status"} | ({"owner", "deadline"} if old["kind"] == "approval" else {"event_date"})
            if set(values) - allowed:
                raise HTTPException(422, "Unsupported fields for this record")
            statuses = {"pending", "approved", "declined", "cancelled"} if old["kind"] == "approval" else {"planned", "done", "cancelled"}
            if "status" in values and values["status"] not in statuses:
                raise HTTPException(422, "Invalid record status")
            if old["kind"] == "milestone" and "event_date" in values and values["event_date"] is None:
                raise HTTPException(422, "Milestones require a date")
            if values:
                assignments = ", ".join(f"{key} = ${index}" for index, key in enumerate(values, 4))
                await conn.execute(
                    f"UPDATE project_records SET {assignments} WHERE user_id = $1 AND project_id = $2 AND id = $3",
                    user_id, project_id, record_id, *values.values(),
                )
            return {"id": record_id}

    async def list(self, user_id, project_id, *, detail=None):
        detail = detail or await project_service.detail(user_id, project_id)
        available = {(s["kind"], str(s["source_id"])): s for s in detail["sources"] if s["available"]}
        scopes = await db.query(
            "SELECT * FROM project_scope_versions WHERE user_id = $1 AND project_id = $2 ORDER BY version DESC", user_id, project_id,
        )
        records = await db.query(
            "SELECT * FROM project_records WHERE user_id = $1 AND project_id = $2 ORDER BY created_at DESC, id", user_id, project_id,
        )
        refs = await db.query(
            "SELECT e.*, s.decisions AS summary_decisions FROM project_record_evidence e "
            "LEFT JOIN meeting_summaries s ON s.user_id = e.user_id AND s.id = e.summary_id AND s.meeting_id = e.meeting_id "
            "AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE) "
            "WHERE e.user_id = $1 AND e.project_id = $2 ORDER BY e.id", user_id, project_id,
        )
        by_record = {}
        for ref in refs:
            _, column = LINKS[ref["kind"]]
            source = available.get((ref["kind"], str(ref[column])))
            accessible = source is not None
            index = ref["decision_index"]
            if index is not None:
                decisions = ref["summary_decisions"] or []
                item = decisions[index] if isinstance(decisions, list) and index < len(decisions) else None
                accessible = accessible and isinstance(item, dict) and isinstance(item.get("text"), str) and text_hash(item["text"]) == ref["decision_hash"]
            by_record.setdefault(ref["record_id"], []).append({
                "id": ref["id"], "kind": ref["kind"], "source_id": ref[column],
                "available": bool(accessible), "title": source["title"] if accessible else "Evidence unavailable",
                "href": source["href"] if accessible else None,
                "summary_id": ref["summary_id"], "decision_index": index,
                # Keep hashes out of the UI; the generated manifest uses these
                # resolved references and record versions, never copied sources.
            })
        for record in records:
            record["evidence"] = by_record.get(record["id"], [])
            record["available"] = all(e["available"] for e in record["evidence"])
            if not record["available"]:
                record.update(title="Record withheld: supporting evidence unavailable", description="", owner="")
        return {"scope": scopes[0] if scopes else None, "scope_history": scopes, "records": records}

    async def meeting_decisions(self, user_id, project_id):
        await project_service.get(user_id, project_id)
        rows = await db.query(
            "SELECT m.id AS meeting_id, m.title, m.date, s.id AS summary_id, s.created_at, s.decisions "
            "FROM project_meeting_links l JOIN meetings m ON m.user_id = l.user_id AND m.id = l.meeting_id "
            "JOIN LATERAL (SELECT * FROM meeting_summaries WHERE user_id = $1 AND meeting_id = m.id ORDER BY created_at DESC, id DESC LIMIT 1) s ON true "
            "WHERE l.user_id = $1 AND m.user_id = $1 AND l.project_id = $2 "
            "AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE) "
            "ORDER BY s.created_at DESC LIMIT 50", user_id, project_id,
        )
        return [{"meeting_id": row["meeting_id"], "meeting_title": row["title"], "summary_id": row["summary_id"],
                 "decision_index": index, "text": item["text"], "date": row["date"]}
                for row in rows for index, item in enumerate(row["decisions"] if isinstance(row["decisions"], list) else [])
                if isinstance(item, dict) and isinstance(item.get("text"), str) and 0 < len(item["text"].strip()) <= 500]


project_knowledge_service = ProjectKnowledgeService()
