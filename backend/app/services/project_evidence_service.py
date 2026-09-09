"""Deterministic, project-only evidence. No semantic retrieval or model calls."""

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import quote

from app import db
from app.services.project_knowledge_service import project_knowledge_service
from app.services.project_service import project_service
from app.services.timezone_utils import local_midnight_utc, local_week_window

MAX_ITEMS = 80
MAX_TEXT = 2000
MAX_CHARACTERS = 60000


async def bounded_rows(sql, user_id, project_id, week_start, now, evidence_keys, select_candidates, *, candidate_filter="true"):
    """Fingerprint all canonical rows in PostgreSQL; return only bounded content.

    Internal queries supply evidence_key/event_at. Requested manifest IDs are
    also returned so selection churn cannot be mistaken for lost authorization.
    Full scans remain necessary to detect edits/deletions without a change log.
    """
    rows = await db.query(
        "WITH evidence AS MATERIALIZED (" + sql + "), signature AS ("
        "SELECT count(*) AS total, md5(COALESCE(string_agg(md5(to_jsonb(e)::text || "
        "COALESCE((e.event_at BETWEEN $3 AND $4)::text, 'false')), '' ORDER BY evidence_key), '')) AS fingerprint FROM evidence e) "
        "SELECT e.*, s.total AS snapshot_total, s.fingerprint AS snapshot_fingerprint FROM signature s "
        "LEFT JOIN LATERAL (SELECT * FROM evidence WHERE evidence_key = ANY($5::text[]) "
        "OR evidence_key IN (SELECT evidence_key FROM evidence WHERE " + candidate_filter +
        " ORDER BY event_at DESC NULLS LAST, evidence_key LIMIT $6)) e ON true",
        user_id, project_id, week_start, now, list(evidence_keys), MAX_ITEMS if select_candidates else 0,
    )
    signature, total = rows[0]["snapshot_fingerprint"], rows[0]["snapshot_total"]
    result = []
    for row in rows:
        if row["evidence_key"] is not None:
            row.pop("snapshot_fingerprint")
            row.pop("snapshot_total")
            result.append(row)
    return result, signature, total - len(result)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


async def project_snapshot(user_id, project_id, now=None, *, evidence_keys=(), select_candidates=True):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    detail = await project_service.detail(user_id, project_id)
    knowledge = await project_knowledge_service.list(user_id, project_id, detail=detail)
    settings = await db.query_one("SELECT timezone FROM settings WHERE user_id = $1", user_id) or {}
    week_start, week_end, tz = local_week_window(settings.get("timezone"), now)
    items = {}
    signatures = {}
    omitted_rows = 0

    def add(key, payload, text, *, kind, occurred_at=None, recorded_at=None, href=None, section=None,
            record_id=None, priority=4, candidate=True, version=None, content_hash=None, fingerprint=True):
        items[key] = {
            "id": key, "text": text[:MAX_TEXT], "kind": kind,
            "occurred_at": occurred_at, "recorded_at": recorded_at,
            "recent_event": bool(occurred_at and week_start <= occurred_at <= now),
            "recent_project_action": bool(recorded_at and week_start <= recorded_at <= now),
            "href": href, "section": section, "record_id": record_id, "version": version,
            "content_hash": content_hash or digest(payload), "priority": priority, "candidate": candidate,
        }
        # Future-dated evidence becoming eligible must stale the prior update,
        # even when no row changes and the local week has not rolled over.
        if fingerprint:
            signatures[key] = digest([digest(payload), items[key]["recent_event"], items[key]["recent_project_action"]])

    project = {k: detail["project"][k] for k in ("name", "description", "target_date", "status")}
    add("project", project, json.dumps(project, default=str), kind="project", section="Overview", priority=0)
    for index, scope in enumerate(knowledge["scope_history"]):
        label = "Current confirmed scope" if index == 0 else "Historical confirmed scope"
        add(f"scope:{scope['id']}", scope, f"{label} revision {scope['version']}: {scope['content'] or '(Scope cleared)'}",
            kind="scope", recorded_at=scope["created_at"], section="Scope", record_id=str(scope["id"]),
            version=scope["version"], priority=0 if index == 0 else 5)
    for record in knowledge["records"]:
        # Fingerprint unavailable markers too, but never include their text in
        # the model or make them available for old generated citations.
        signatures[f"record-state:{record['id']}"] = digest(record)
        if not record["available"]:
            continue
        text = json.dumps({k: record[k] for k in ("kind", "title", "description", "owner", "status", "event_date", "deadline", "supersedes_id")}, default=str)
        add(f"record:{record['id']}", record, text, kind=record["kind"], recorded_at=record["updated_at"],
            occurred_at=local_midnight_utc(record["event_date"], tz)
            if record["kind"] == "decision" else None,
            section={"decision": "Decisions", "approval": "Approvals", "milestone": "Milestones"}[record["kind"]],
            record_id=str(record["id"]), version=record["version"], priority=1 if record["status"] != "superseded" else 5)
    # Association timestamps are separate from original source event times.
    signatures["associations"] = digest([{k: s[k] for k in ("id", "kind", "source_id", "linked_at", "available")} for s in detail["sources"]])
    messages, signatures["messages"], omitted = await bounded_rows(
        "SELECT 'email:inbound:' || e.id AS evidence_key, e.received_at AS event_at, e.id, e.thread_id, e.subject, e.from_email AS participant, e.received_at AS occurred_at, "
        "left(e.body, 2000) AS excerpt, md5(e.body) AS body_hash, 'inbound' AS direction "
        "FROM emails e JOIN project_thread_links l ON l.user_id = e.user_id AND l.thread_id = e.thread_id "
        "WHERE e.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 UNION ALL "
        "SELECT 'email:sent:' || e.id, e.sent_at, e.id, e.thread_id, e.subject, array_to_string(e.to_emails, ', '), e.sent_at, left(e.body, 2000), md5(e.body), 'sent' "
        "FROM sent_emails e JOIN project_thread_links l ON l.user_id = e.user_id AND l.thread_id = e.thread_id "
        "WHERE e.user_id = $1 AND l.user_id = $1 AND l.project_id = $2", user_id, project_id,
        week_start, now, evidence_keys, select_candidates,
    )
    omitted_rows += omitted
    for message in messages:
        key = f"email:{message['direction']}:{message['id']}"
        text = f"{message['direction']} email: {message['subject']}\nParticipant: {message['participant']}\n{message['excerpt'] or ''}"
        add(key, message, text, kind="email", occurred_at=message["occurred_at"], section="Sources", record_id=message["thread_id"],
            href=f"/inbox/{quote(message['id'], safe='')}" if message["direction"] == "inbound" else None,
            priority=3 if message["occurred_at"] and message["occurred_at"] >= week_start else 5, fingerprint=False)
    meetings = await db.query(
        "SELECT m.id, m.title, m.status, COALESCE(m.started_at, m.date) AS occurred_at, "
        "left(m.user_notes, 2000) AS notes, md5(m.user_notes) AS notes_hash, "
        "(SELECT md5(COALESCE(string_agg(md5(t.text), '' ORDER BY t.id), '')) FROM meeting_transcript_segments t "
        "WHERE t.user_id = $1 AND t.meeting_id = m.id) AS transcript_hash "
        "FROM meetings m JOIN project_meeting_links l ON l.user_id = m.user_id AND l.meeting_id = m.id "
        "WHERE m.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 "
        "AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)", user_id, project_id,
    )
    for meeting in meetings:
        add(f"meeting:{meeting['id']}", meeting, f"Meeting: {meeting['title']}\nStatus: {meeting['status']}\nNotes: {meeting['notes'] or '(none)'}",
            kind="meeting", occurred_at=meeting["occurred_at"],
            href=f"/meetings/{'live/' if meeting['status'] == 'recording' else ''}{meeting['id']}", priority=4)
    summaries, signatures["summaries"], omitted = await bounded_rows(
        "SELECT 'summary:' || s.id AS evidence_key, s.created_at AS event_at, s.id, s.meeting_id, left(s.tldr, 600) AS tldr, s.created_at, "
        "left(s.decisions::text, 600) AS decisions, left(s.action_items::text, 600) AS action_items, "
        "md5(jsonb_build_array(s.tldr, s.decisions, s.action_items, s.enhanced_notes)::text) AS content_hash, "
        "row_number() OVER (PARTITION BY s.meeting_id ORDER BY s.created_at DESC, s.id DESC) AS version_rank "
        "FROM meeting_summaries s JOIN project_meeting_links l ON l.user_id = s.user_id AND l.meeting_id = s.meeting_id "
        "WHERE s.user_id = $1 AND l.user_id = $1 AND l.project_id = $2 "
        "AND EXISTS (SELECT 1 FROM settings WHERE user_id = $1 AND meeting_capture_mode IS TRUE)", user_id, project_id,
        week_start, now, evidence_keys, select_candidates, candidate_filter="version_rank = 1",
    )
    omitted_rows += omitted
    meeting_map = {m["id"]: m for m in meetings}
    for summary in summaries:
        meeting = meeting_map.get(summary["meeting_id"])
        if not meeting:
            continue
        add(f"summary:{summary['id']}", summary,
            f"Generated meeting summary: {(meeting['title'] or 'Untitled meeting')[:100]}\nOverview: {summary['tldr'] or '(none)'}"
            f"\nDecisions excerpt: {summary['decisions']}\nAction items excerpt: {summary['action_items']}",
            kind="meeting_summary", occurred_at=meeting["occurred_at"], href=f"/meetings/{meeting['id']}",
            version=str(summary["id"]), content_hash=summary["content_hash"], candidate=summary["version_rank"] == 1, fingerprint=False)
    commitments = await db.query(
        "SELECT c.id, c.text, c.direction, c.status, c.deadline, c.created_at, c.resolved_at, c.project_changed_at "
        "FROM commitments c JOIN project_commitment_links l ON l.user_id = c.user_id AND l.commitment_id = c.id "
        "WHERE c.user_id = $1 AND l.user_id = $1 AND l.project_id = $2", user_id, project_id,
    )
    for commitment in commitments:
        add(f"commitment:{commitment['id']}", commitment, json.dumps(commitment, default=str), kind="commitment",
            occurred_at=commitment["project_changed_at"] or commitment["resolved_at"] or commitment["created_at"],
            href=f"/commitments?direction=all&status={commitment['status']}#commitment-{commitment['id']}", priority=2)
    # Log entries have no copied source text. Only accessible sources/records
    # may enrich them. Old events stay original; linking today is a project action.
    activity, signatures["activity"], omitted = await bounded_rows(
        "SELECT 'activity:' || id AS evidence_key, occurred_at AS event_at, id, action, source_kind, details, occurred_at FROM project_activity "
        "WHERE user_id = $1 AND project_id = $2", user_id, project_id,
        week_start, now, evidence_keys, select_candidates,
    )
    omitted_rows += omitted
    accessible_sources = {(s["kind"], str(s["source_id"])) for s in detail["sources"] if s["available"]}
    for event in activity:
        info = event["details"]
        if event["source_kind"] and (event["source_kind"], str(info.get("source_id"))) not in accessible_sources:
            continue
        if info.get("record_id") and f"record:{info['record_id']}" not in items and f"scope:{info['record_id']}" not in items:
            continue
        add(f"activity:{event['id']}", event, json.dumps(event, default=str), kind="project_action",
            recorded_at=event["occurred_at"], section="Activity", priority=3 if event["occurred_at"] >= week_start else 6, fingerprint=False)
    ordered = sorted((item for item in items.values() if item["candidate"]),
                     key=lambda item: (item["priority"], -(item["recorded_at"] or item["occurred_at"] or now).timestamp(), item["id"]))
    selected = []
    characters = 2  # JSON array brackets
    for item in ordered:
        payload = {k: v for k, v in item.items() if k not in ("priority", "candidate", "href", "section", "record_id", "content_hash")}
        size = len(json.dumps(payload, default=str)) + (2 if selected else 0)
        if len(selected) >= MAX_ITEMS or characters + size > MAX_CHARACTERS:
            continue
        characters += size
        selected.append(payload)
    return {"items": items, "selected": selected, "fingerprint": digest({"timezone": tz, "evidence": signatures}),
            "week_start": week_start, "week_end": week_end, "timezone": tz, "as_of": now,
            "omitted_count": omitted_rows + len(ordered) - len(selected)}
