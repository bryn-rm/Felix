"""
Job Search Mode service.

Tracks each job application and its progress, auto-populated from Gmail +
Calendar activity. Gated per-user by ``settings.job_search_mode`` and fails
closed: when the flag is off/unset nothing here runs.

Pipeline (cost-controlled — keep these two facts intact):
  1. ``_looks_job_related`` — a pure-Python deterministic gate (ATS sender
     domains, keyword hits, known tracked thread/contact). No model call.
  2. Only emails that clear the gate hit ``ai_service.detect_job_activity``
     (Sonnet — stage classification corrupts the board if wrong and Haiku is
     unreliable on phone_screen vs interview vs offer).

Identity is the Gmail ``thread_id`` first (stable across stages); company+role
is a fuzzy cross-thread stitch fallback only, never a merge key (ATS bots and
rotating recruiter/scheduler/hiring-manager make contact_email unreliable, and
exact company/role drift with LLM extraction).

Entry points:
  scan_email(user_id, email)        — inbound, from inbox-sync
  scan_sent(user_id, sent_email)    — outbound, from inbox-sync sent mirror
  log_outbound_event(user_id, …)    — from POST /emails/{id}/send
plus board CRUD, suggestion resolution, and on-demand follow-up drafting.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app import db
from app.services.ai_service import ai_service
from app.utils.background import spawn

logger = logging.getLogger(__name__)


# Confidence ≥ this auto-creates/advances a job; below it becomes a pending
# suggestion the user confirms. Tunable later from job_suggestions telemetry.
AUTO_CONFIDENCE_FLOOR = 0.8

# Fuzzy cross-thread stitch only fires when the normalised company matches AND
# role tokens overlap at least this much — keeps distinct roles at one company
# as separate jobs instead of silently merging them.
ROLE_MATCH_THRESHOLD = 0.6

# Forward-only positive ladder. Terminal sinks override unconditionally.
POSITIVE_LADDER = ["saved", "applied", "phone_screen", "interview", "offer"]
TERMINAL_STATUSES = {"rejected", "withdrawn", "accepted"}
ALL_STATUSES = set(POSITIVE_LADDER) | TERMINAL_STATUSES

# Applicant Tracking System sender domains — a hit is sufficient for the gate.
ATS_DOMAINS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkday.com", "workday.com",
    "bamboohr.com", "smartrecruiters.com", "workable.com", "jobvite.com",
    "icims.com", "taleo.net", "successfactors.com", "breezy.hr", "gem.com",
    "hire.lever.co", "us.greenhouse-mail.io", "greenhouse-mail.io",
}

# Keyword hits in subject/body. Intentionally broad — this is a recall gate, the
# model does precision. A false pass costs one extraction; a false reject loses
# the job silently.
JOB_KEYWORDS = (
    "your application", "thank you for applying", "application received",
    "application has been", "we received your application", "next steps",
    "interview", "phone screen", "recruiter", "recruiting", "talent",
    "hiring team", "hiring manager", "job offer", "offer letter",
    "position", "the role", "candidate", "schedule a call", "screening call",
    "move forward", "not moving forward", "unfortunately", "regret to inform",
    "assessment", "take-home", "coding challenge", "onsite", "we'd love to",
)


class JobTrackerService:

    # ------------------------------------------------------------------
    # Inbound / outbound scan entry points
    # ------------------------------------------------------------------

    async def scan_email(self, user_id: str, email: dict) -> dict | None:
        """Inbound email → maybe create/advance a job or raise a suggestion."""
        return await self._process(
            user_id=user_id,
            email=email,
            source_kind="email",
            source_id=email.get("id"),
            event_type="email_in",
            sender=(email.get("from_email") or email.get("from") or ""),
        )

    async def scan_sent(self, user_id: str, sent_email: dict) -> dict | None:
        """Outbound email → often the first signal (the application itself)."""
        # Normalise the sent-email shape onto the fields the gate/model read.
        to_emails = list(sent_email.get("to_emails") or [])
        recipient = (
            sent_email.get("to_email")
            or sent_email.get("to")
            or (to_emails[0] if to_emails else "")
        )
        payload = {
            **sent_email,
            "from_email": sent_email.get("from_email") or "",
            "to_email": ", ".join(to_emails) if to_emails else recipient,
        }
        return await self._process(
            user_id=user_id,
            email=payload,
            source_kind="email",
            source_id=sent_email.get("id"),
            event_type="email_out",
            # For outbound, the "counterparty" used by the gate is the recipient.
            sender=recipient,
        )

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    async def _process(
        self,
        *,
        user_id: str,
        email: dict,
        source_kind: str,
        source_id: str | None,
        event_type: str,
        sender: str,
    ) -> dict | None:
        # Fail closed — never run detection when the gate flag is off/unset.
        if not await _is_enabled(user_id):
            return None

        known = await _load_known(user_id)
        if not _looks_job_related(email, known):
            return None

        detected = await ai_service.detect_job_activity(email=email, user_id=user_id)
        if not detected or not detected.get("is_job_related"):
            return None

        company = (detected.get("company") or "").strip()
        role_title = (detected.get("role_title") or "").strip()
        if not company or not role_title:
            # Without both we can neither display nor fuzzy-match safely.
            return None

        confidence = _coerce_float(detected.get("confidence"), 0.0)
        thread_id = email.get("thread_id") or None
        match = await self._match_job(user_id, thread_id, company, role_title)

        if confidence >= AUTO_CONFIDENCE_FLOOR:
            job = await self._upsert_job(
                user_id=user_id,
                match=match,
                detected=detected,
                thread_id=thread_id,
                source_id=source_id,
                event_type=event_type,
                occurred_at=_occurred_at(email),
            )
            if job:
                await self._reconcile_suggestions(user_id, thread_id, job["id"])
            return job

        await self._upsert_suggestion(
            user_id=user_id,
            match=match,
            detected=detected,
            thread_id=thread_id,
            source_kind=source_kind,
            source_id=source_id,
            confidence=confidence,
        )
        return None

    async def _match_job(
        self, user_id: str, thread_id: str | None, company: str, role_title: str,
    ) -> dict | None:
        """Identity: thread_id first, then a conservative fuzzy company+role stitch."""
        if thread_id:
            row = await db.query_one(
                "SELECT * FROM job_applications WHERE user_id = $1 AND thread_id = $2",
                user_id, thread_id,
            )
            if row:
                return row

        norm_company = _normalize_company(company)
        if not norm_company:
            return None
        role_tokens = _token_set(role_title)
        candidates = await db.query(
            "SELECT * FROM job_applications WHERE user_id = $1 AND status NOT IN "
            "('rejected','withdrawn','accepted')",
            user_id,
        )
        for cand in candidates:
            if _normalize_company(cand.get("company") or "") != norm_company:
                continue
            if _jaccard(role_tokens, _token_set(cand.get("role_title") or "")) >= ROLE_MATCH_THRESHOLD:
                return cand
        return None

    async def _upsert_job(
        self,
        *,
        user_id: str,
        match: dict | None,
        detected: dict,
        thread_id: str | None,
        source_id: str | None,
        event_type: str,
        occurred_at: datetime,
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        stage = detected.get("stage")
        company = (detected.get("company") or "").strip()
        role_title = (detected.get("role_title") or "").strip()
        contact_name = (detected.get("contact_name") or "").strip() or None
        contact_email = (detected.get("contact_email") or "").strip().lower() or None
        summary = (detected.get("summary") or "").strip() or None
        confidence = _coerce_float(detected.get("confidence"), 0.0)

        if match:
            new_status = apply_status(match.get("status") or "applied", stage)
            next_action, next_action_at = _next_action_for_stage(new_status, now)
            applied_at = match.get("applied_at") or (now if new_status != "saved" else None)
            job = await db.query_one(
                """
                UPDATE job_applications
                SET status           = $3,
                    last_activity_at  = $4,
                    next_action       = $5,
                    next_action_at    = $6,
                    contact_name      = COALESCE(contact_name, $7),
                    contact_email     = COALESCE(contact_email, $8),
                    thread_id         = COALESCE(thread_id, $9),
                    applied_at        = COALESCE(applied_at, $10),
                    updated_at        = NOW()
                WHERE id = $1 AND user_id = $2
                RETURNING *
                """,
                match["id"], user_id, new_status, now, next_action, next_action_at,
                contact_name, contact_email, thread_id, applied_at,
            )
            if job and new_status != (match.get("status") or "applied"):
                await self._add_event(
                    user_id, job["id"], "status_change",
                    title=f"{match.get('status')} → {new_status}",
                    detail=summary, occurred_at=now,
                )
        else:
            status = stage if stage in ALL_STATUSES else "applied"
            next_action, next_action_at = _next_action_for_stage(status, now)
            applied_at = now if status not in ("saved",) else None
            # Race-safe insert: scan_email (inbound loop), scan_sent (spawned
            # sent mirror) and the catch-up sweeps can all reach this for the
            # same thread concurrently, and _match_job's read may be stale. The
            # UNIQUE (user_id, thread_id) WHERE thread_id IS NOT NULL index
            # (migration 015) plus ON CONFLICT collapse the race onto one row
            # instead of two duplicate cards. thread_id NULL skips the predicate
            # and inserts normally (no identity to dedupe on).
            job = await db.query_one(
                """
                INSERT INTO job_applications
                    (user_id, thread_id, company, role_title, status, source,
                     contact_name, contact_email, applied_at, last_activity_at,
                     next_action, next_action_at, confidence)
                VALUES ($1, $2, $3, $4, $5, 'email', $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (user_id, thread_id) WHERE thread_id IS NOT NULL
                DO UPDATE SET
                    last_activity_at = EXCLUDED.last_activity_at,
                    contact_name     = COALESCE(job_applications.contact_name, EXCLUDED.contact_name),
                    contact_email    = COALESCE(job_applications.contact_email, EXCLUDED.contact_email),
                    updated_at       = NOW()
                RETURNING *
                """,
                user_id, thread_id, company, role_title, status,
                contact_name, contact_email, applied_at, now,
                next_action, next_action_at, confidence,
            )

        if job:
            await self._add_event(
                user_id, job["id"], event_type,
                title=summary or f"{event_type} — {company}",
                detail=summary, source_kind="email", source_id=source_id,
                occurred_at=occurred_at,
            )
        return job

    async def _upsert_suggestion(
        self,
        *,
        user_id: str,
        match: dict | None,
        detected: dict,
        thread_id: str | None,
        source_kind: str,
        source_id: str | None,
        confidence: float,
    ) -> dict | None:
        # Idempotent on (user_id, source_kind, source_id) — re-scans don't pile up.
        if source_id:
            existing = await db.query_one(
                "SELECT id FROM job_suggestions WHERE user_id = $1 AND source_kind = $2 "
                "AND source_id = $3",
                user_id, source_kind, source_id,
            )
            if existing:
                return None
        try:
            return await db.insert(
                "job_suggestions",
                {
                    "user_id":         user_id,
                    "source_kind":     source_kind,
                    "source_id":       source_id,
                    "thread_id":       thread_id,
                    "company":         (detected.get("company") or "").strip() or None,
                    "role_title":      (detected.get("role_title") or "").strip() or None,
                    "contact_name":    (detected.get("contact_name") or "").strip() or None,
                    "contact_email":   (detected.get("contact_email") or "").strip().lower() or None,
                    "proposed_status": detected.get("stage"),
                    "proposed_job_id": match["id"] if match else None,
                    "summary":         (detected.get("summary") or "").strip() or None,
                    "confidence":      confidence,
                    "status":          "pending",
                },
            )
        except asyncpg.UniqueViolationError:
            # The check above is best-effort; a concurrent scan of the same
            # source message can slip in between. The uq_job_suggestions_source
            # index is the real guard — treat the conflict as "already raised"
            # rather than letting it propagate (which would leave job_scanned_at
            # NULL and put this message into a perpetual retry loop).
            return None

    async def _reconcile_suggestions(
        self, user_id: str, thread_id: str | None, job_id: str,
    ) -> None:
        """Auto-dismiss pending suggestions for a job that now exists (point 5)."""
        if not thread_id:
            return
        await db.execute(
            """
            UPDATE job_suggestions
            SET status = 'auto_dismissed', resolved_at = NOW(), proposed_job_id = $3
            WHERE user_id = $1 AND thread_id = $2 AND status = 'pending'
            """,
            user_id, thread_id, job_id,
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def _add_event(
        self,
        user_id: str,
        job_id: str,
        event_type: str,
        *,
        title: str | None = None,
        detail: str | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict | None:
        occurred_at = occurred_at or datetime.now(timezone.utc)
        # De-dupe sourced events (inbox-sync replays the same message).
        if source_id:
            existing = await db.query_one(
                "SELECT id FROM job_events WHERE user_id = $1 AND job_id = $2 "
                "AND source_kind = $3 AND source_id = $4",
                user_id, job_id, source_kind, source_id,
            )
            if existing:
                return None
        return await db.insert(
            "job_events",
            {
                "user_id":     user_id,
                "job_id":      job_id,
                "event_type":  event_type,
                "title":       (title or "")[:300] or None,
                "detail":      (detail or "")[:2000] or None,
                "source_kind": source_kind,
                "source_id":   source_id,
                "occurred_at": occurred_at,
            },
        )

    async def add_event(
        self, user_id: str, job_id: str, event_type: str, *,
        title: str | None = None, detail: str | None = None,
        source_kind: str = "manual", source_id: str | None = None,
    ) -> dict | None:
        """Manual note/event from the UI, or a sourced event when ``source_id`` is
        given (deduped per (job, source_kind, source_id) by ``_add_event``)."""
        job = await db.query_one(
            "SELECT id FROM job_applications WHERE id = $1 AND user_id = $2",
            job_id, user_id,
        )
        if not job:
            return None
        now = datetime.now(timezone.utc)
        row = await self._add_event(
            user_id, job_id, event_type, title=title, detail=detail,
            source_kind=source_kind, source_id=source_id, occurred_at=now,
        )
        await db.execute(
            "UPDATE job_applications SET last_activity_at = $3, updated_at = NOW() "
            "WHERE id = $1 AND user_id = $2",
            job_id, user_id, now,
        )
        return row

    async def log_outbound_event(
        self, user_id: str, sent_email: dict, kind: str = "email_out",
    ) -> dict | None:
        """From POST /emails/{id}/send — log an outbound event on a tracked job.

        Near-free: we own this path, so no gate/model call. Matches the job by
        thread_id (preferred) or recipient. ``kind`` is ``email_out`` or
        ``follow_up_sent`` (when the draft originated from draft_follow_up).
        """
        if not await _is_enabled(user_id):
            return None
        thread_id = sent_email.get("thread_id") or None
        recipient = (sent_email.get("to_email") or sent_email.get("to") or "").strip().lower()

        job = None
        matched_by_thread = False
        if thread_id:
            job = await db.query_one(
                "SELECT * FROM job_applications WHERE user_id = $1 AND thread_id = $2",
                user_id, thread_id,
            )
            matched_by_thread = job is not None
        if not job and recipient:
            # Recipient is a weak signal: one recruiter/ATS address can front
            # several of the user's applications. Only attribute when it's
            # unambiguous — a single active job carries this contact_email —
            # otherwise we'd credit the send to (and clear the badge of) the
            # wrong job. Ambiguous → don't guess.
            candidates = await db.query(
                "SELECT * FROM job_applications WHERE user_id = $1 "
                "AND LOWER(contact_email) = $2 "
                "AND status NOT IN ('rejected','withdrawn','accepted')",
                user_id, recipient,
            )
            if len(candidates) == 1:
                job = candidates[0]
        if not job:
            return None

        now = datetime.now(timezone.utc)
        # Sending on a thread whose reminder is already DUE is the follow-up —
        # upgrade and clear the badge. Gate this on matched_by_thread: only a
        # send on the application's own thread counts as satisfying its
        # follow-up. A recipient-matched send (no thread correlation) is logged
        # as plain outbound and must NOT clear a reminder, or any mail to a
        # shared recruiter address would silently dismiss an unrelated job's
        # nudge. A reply while the reminder is still in the future likewise
        # leaves the future reminder intact.
        next_at = job.get("next_action_at")
        if kind == "email_out" and matched_by_thread and next_at and next_at <= now:
            kind = "follow_up_sent"
        row = await self._add_event(
            user_id, job["id"], kind,
            title=(sent_email.get("subject") or kind),
            detail=(sent_email.get("body") or "")[:2000] or None,
            source_kind="email", source_id=sent_email.get("id"),
            occurred_at=now,
        )
        # A sent follow-up clears the due badge.
        if kind == "follow_up_sent":
            await db.execute(
                "UPDATE job_applications SET next_action = NULL, next_action_at = NULL, "
                "last_activity_at = $3, updated_at = NOW() WHERE id = $1 AND user_id = $2",
                job["id"], user_id, now,
            )
        else:
            await db.execute(
                "UPDATE job_applications SET last_activity_at = $3, updated_at = NOW() "
                "WHERE id = $1 AND user_id = $2",
                job["id"], user_id, now,
            )
        return row

    # ------------------------------------------------------------------
    # Board CRUD
    # ------------------------------------------------------------------

    async def create_manual(self, user_id: str, patch: dict) -> dict | None:
        now = datetime.now(timezone.utc)
        status = patch.get("status") or "applied"
        if status not in ALL_STATUSES:
            status = "applied"
        company = (patch.get("company") or "").strip()
        role_title = (patch.get("role_title") or "").strip()
        if not company or not role_title:
            raise ValueError("company and role_title are required")
        job = await db.insert(
            "job_applications",
            {
                "user_id":       user_id,
                "company":       company,
                "role_title":    role_title,
                "location":      (patch.get("location") or "").strip() or None,
                "job_url":       (patch.get("job_url") or "").strip() or None,
                "status":        status,
                "source":        "manual",
                "contact_name":  (patch.get("contact_name") or "").strip() or None,
                "contact_email": (patch.get("contact_email") or "").strip().lower() or None,
                "compensation":  (patch.get("compensation") or "").strip() or None,
                "notes":         (patch.get("notes") or "").strip() or None,
                "applied_at":    now if status not in ("saved",) else None,
                "last_activity_at": now,
                "confidence":    1.0,
            },
        )
        if job:
            await self._add_event(
                user_id, job["id"], "applied" if status != "saved" else "note",
                title="Added manually", source_kind="manual", occurred_at=now,
            )
        return job

    async def update(self, user_id: str, job_id: str, patch: dict) -> dict | None:
        """Patch fields/status (drag-to-stage hits this). Logs a status_change event."""
        current = await db.query_one(
            "SELECT * FROM job_applications WHERE id = $1 AND user_id = $2",
            job_id, user_id,
        )
        if not current:
            return None

        allowed = {
            "company", "role_title", "location", "job_url", "status", "contact_name",
            "contact_email", "compensation", "notes", "next_action", "next_action_at",
        }
        fields = {k: v for k, v in patch.items() if k in allowed}
        if "status" in fields and fields["status"] not in ALL_STATUSES:
            raise ValueError(f"invalid status: {fields['status']}")

        # On a real stage change, recompute the follow-up reminder the same way
        # auto-detection does — unless the caller explicitly supplied follow-up
        # fields. Without this, moving to a terminal status leaves a stale due
        # badge on a closed card, and moving to interview/offer never creates the
        # expected thank-you/review reminder.
        if "status" in fields and fields["status"] != current.get("status"):
            caller_set_action = "next_action" in patch or "next_action_at" in patch
            if not caller_set_action:
                na, na_at = _next_action_for_stage(
                    fields["status"], datetime.now(timezone.utc),
                )
                fields["next_action"] = na
                fields["next_action_at"] = na_at

        if not fields:
            return current

        sets = []
        args: list[Any] = [job_id, user_id]
        for i, (k, v) in enumerate(fields.items(), start=3):
            sets.append(f"{k} = ${i}")
            args.append(v)
        sql = (
            "UPDATE job_applications SET " + ", ".join(sets)
            + ", updated_at = NOW() WHERE id = $1 AND user_id = $2 RETURNING *"
        )
        row = await db.query_one(sql, *args)

        new_status = fields.get("status")
        if row and new_status and new_status != current.get("status"):
            await self._add_event(
                user_id, job_id, "status_change",
                title=f"{current.get('status')} → {new_status}",
                source_kind="manual", occurred_at=datetime.now(timezone.utc),
            )
        return row

    async def delete(self, user_id: str, job_id: str) -> bool:
        result = await db.execute(
            "DELETE FROM job_applications WHERE id = $1 AND user_id = $2",
            job_id, user_id,
        )
        return result.endswith(("1", "DELETE 1")) or "1" in result

    async def get(self, user_id: str, job_id: str) -> dict | None:
        job = await db.query_one(
            "SELECT * FROM job_applications WHERE id = $1 AND user_id = $2",
            job_id, user_id,
        )
        if not job:
            return None
        events = await db.query(
            "SELECT * FROM job_events WHERE user_id = $1 AND job_id = $2 "
            "ORDER BY occurred_at DESC",
            user_id, job_id,
        )
        return {"job": job, "events": events}

    async def list_board(self, user_id: str) -> dict:
        """Active jobs grouped by status for the Kanban, plus due-badge state."""
        rows = await db.query(
            "SELECT * FROM job_applications WHERE user_id = $1 "
            "ORDER BY last_activity_at DESC NULLS LAST, created_at DESC",
            user_id,
        )
        now = datetime.now(timezone.utc)
        columns: dict[str, list[dict]] = {s: [] for s in POSITIVE_LADDER}
        # Terminal statuses share a collapsed "closed" column on the board.
        columns["closed"] = []
        for r in rows:
            due = r.get("next_action_at")
            r["is_due"] = bool(due and due <= now)
            status = r.get("status")
            if status in TERMINAL_STATUSES:
                columns["closed"].append(r)
            else:
                columns.setdefault(status or "applied", []).append(r)
        counts = {k: len(v) for k, v in columns.items()}
        return {"columns": columns, "counts": counts, "total": len(rows)}

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    async def list_suggestions(self, user_id: str) -> list[dict]:
        return await db.query(
            "SELECT * FROM job_suggestions WHERE user_id = $1 AND status = 'pending' "
            "ORDER BY created_at DESC",
            user_id,
        )

    async def resolve_suggestion(
        self, user_id: str, suggestion_id: str, accept: bool,
    ) -> dict | None:
        """Accept → create/advance the job (+ reconcile). Both accept and dismiss
        stamp resolved_at + keep confidence as the labeled detection outcome."""
        sug = await db.query_one(
            "SELECT * FROM job_suggestions WHERE id = $1 AND user_id = $2 AND status = 'pending'",
            suggestion_id, user_id,
        )
        if not sug:
            return None

        job = None
        if accept:
            detected = {
                "company":       sug.get("company"),
                "role_title":    sug.get("role_title"),
                "stage":         sug.get("proposed_status"),
                "contact_name":  sug.get("contact_name"),
                "contact_email": sug.get("contact_email"),
                "confidence":    sug.get("confidence") or AUTO_CONFIDENCE_FLOOR,
                "summary":       sug.get("summary"),
            }
            match = None
            if sug.get("proposed_job_id"):
                match = await db.query_one(
                    "SELECT * FROM job_applications WHERE id = $1 AND user_id = $2",
                    sug["proposed_job_id"], user_id,
                )
            if not match:
                match = await self._match_job(
                    user_id, sug.get("thread_id"),
                    sug.get("company") or "", sug.get("role_title") or "",
                )
            job = await self._upsert_job(
                user_id=user_id, match=match, detected=detected,
                thread_id=sug.get("thread_id"), source_id=sug.get("source_id"),
                event_type="email_in", occurred_at=datetime.now(timezone.utc),
            )
            if job:
                await self._reconcile_suggestions(user_id, sug.get("thread_id"), job["id"])

        # resolved_at + confidence are eval telemetry — do not drop (point 7).
        await db.execute(
            "UPDATE job_suggestions SET status = $3, resolved_at = NOW() "
            "WHERE id = $1 AND user_id = $2",
            suggestion_id, user_id, "accepted" if accept else "dismissed",
        )
        return job

    # ------------------------------------------------------------------
    # On-demand follow-up draft (review-first; never auto-sent)
    # ------------------------------------------------------------------

    async def draft_follow_up(self, user_id: str, job_id: str) -> dict | None:
        """Draft a thank-you / nudge to the interviewer. Persists a reviewable
        draft via the existing drafts table; sending later flows through
        /emails/{id}/send → log_outbound_event(follow_up_sent)."""
        job = await db.query_one(
            "SELECT * FROM job_applications WHERE id = $1 AND user_id = $2",
            job_id, user_id,
        )
        if not job:
            return None

        # Reply onto the most recent inbound email of the job's thread so the
        # draft threads correctly and the existing send route just works.
        last_in = await db.query_one(
            "SELECT source_id FROM job_events WHERE user_id = $1 AND job_id = $2 "
            "AND event_type = 'email_in' AND source_id IS NOT NULL "
            "ORDER BY occurred_at DESC LIMIT 1",
            user_id, job_id,
        )
        email = None
        if last_in and last_in.get("source_id"):
            email = await db.query_one(
                "SELECT * FROM emails WHERE id = $1 AND user_id = $2",
                last_in["source_id"], user_id,
            )
        if not email:
            # No threaded inbound to reply to (e.g. manual job) — caller surfaces
            # this so the user can reach out manually instead.
            return {"draft": None, "reason": "no_threaded_email"}

        # Don't clobber a draft the user is already editing on this email. The
        # upsert below overwrites draft_text and resets edited_text to NULL, so a
        # blind regenerate would silently discard in-progress edits. If an edited
        # draft exists, return it untouched and let the caller surface it.
        existing_draft = await db.query_one(
            "SELECT * FROM drafts WHERE email_id = $1 AND user_id = $2",
            email["id"], user_id,
        )
        if existing_draft and (existing_draft.get("edited_text") or "").strip():
            return {
                "draft": existing_draft,
                "email_id": email["id"],
                "thread_id": job.get("thread_id"),
                "reason": "existing_draft_preserved",
            }

        contact = await db.query_one(
            "SELECT * FROM contacts WHERE user_id = $1 AND email = $2",
            user_id, email.get("from_email") or "",
        ) or {}
        settings_row = await db.query_one(
            "SELECT display_name, style_profile FROM settings WHERE user_id = $1",
            user_id,
        ) or {}
        style_profile = settings_row.get("style_profile") or {}
        user_name = settings_row.get("display_name") or "User"

        intent = (
            f"Write a brief, warm follow-up to {job.get('contact_name') or 'the interviewer'} "
            f"about the {job.get('role_title')} role at {job.get('company')}. "
            "Thank them for their time, reiterate interest, and invite next steps. "
            "Keep it concise and professional."
        )

        full_text = ""
        async for chunk in ai_service.draft_reply(
            email=dict(email),
            thread_history=[],
            contact=contact,
            style_profile=style_profile,
            user_name=user_name,
            user_intent=intent,
            user_id=user_id,
            quota_scope="interactive",
        ):
            full_text += chunk

        row = await db.upsert(
            "drafts",
            {
                "email_id":    email["id"],
                "user_id":     user_id,
                "draft_text":  full_text,
                "status":      "pending",
                "edited_text": None,
            },
            conflict_columns=["email_id", "user_id"],
        )
        await db.execute(
            "UPDATE emails SET draft_generated = TRUE WHERE id = $1 AND user_id = $2",
            email["id"], user_id,
        )
        return {
            "draft": row,
            "email_id": email["id"],
            "thread_id": job.get("thread_id"),
        }


job_tracker_service = JobTrackerService()


# ---------------------------------------------------------------------------
# Status transition rule
# ---------------------------------------------------------------------------


def apply_status(current: str, detected: str | None) -> str:
    """Forward-only on the positive ladder; terminal sinks override unconditionally.

    - rejected / withdrawn / accepted apply at any stage (terminal).
    - Once terminal, a later positive signal does NOT resurrect the job.
    - On the positive ladder, only forward moves stick (offer→phone_screen is ignored).
    """
    current = current if current in ALL_STATUSES else "applied"
    if detected not in ALL_STATUSES:
        return current
    if detected in TERMINAL_STATUSES:
        return detected
    if current in TERMINAL_STATUSES:
        return current
    # Both on the positive ladder — forward-only.
    if POSITIVE_LADDER.index(detected) > POSITIVE_LADDER.index(current):
        return detected
    return current


# ---------------------------------------------------------------------------
# Deterministic pre-filter (no LLM)
# ---------------------------------------------------------------------------


def _looks_job_related(email: dict, known: dict | None = None) -> bool:
    """Cheap heuristic gate. Returns True if the email is worth an extraction call.

    A hit on any of: ATS sender domain, known tracked thread/contact, or a
    job-ish keyword in the subject/body. Recall-biased on purpose — the model
    decides precision.
    """
    known = known or {}
    sender = (email.get("from_email") or email.get("from") or "").strip().lower()
    # Outbound emails carry the recipient in to_email/to.
    recipient = (email.get("to_email") or email.get("to") or "").strip().lower()

    domain = sender.split("@")[-1] if "@" in sender else ""
    if domain and any(domain == d or domain.endswith("." + d) for d in ATS_DOMAINS):
        return True
    for addr in (recipient or "").split(","):
        rdomain = addr.strip().split("@")[-1] if "@" in addr else ""
        if rdomain and any(rdomain == d or rdomain.endswith("." + d) for d in ATS_DOMAINS):
            return True

    thread_id = email.get("thread_id")
    if thread_id and thread_id in known.get("thread_ids", set()):
        return True
    if sender and sender in known.get("contacts", set()):
        return True

    text = ((email.get("subject") or "") + " " + (email.get("body") or "")).lower()
    return any(kw in text for kw in JOB_KEYWORDS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|"
    r"pbc|plc|gmbh|sa|ag|holdings|group|technologies|technology|labs|the)\b",
    re.IGNORECASE,
)
_STOPWORD_TOKENS = {
    "engineer", "senior", "staff", "lead", "principal", "junior", "mid",
    "the", "of", "and", "a", "an", "ii", "iii", "iv", "i",
}


def _normalize_company(name: str) -> str:
    name = (name or "").lower()
    name = _COMPANY_SUFFIXES.sub(" ", name)
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return " ".join(name.split())


def _token_set(text: str) -> set[str]:
    text = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return {t for t in text.split() if t and t not in _STOPWORD_TOKENS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _next_action_for_stage(status: str, now: datetime) -> tuple[str | None, datetime | None]:
    """Sensible default next action + due time that drives the board's due badge."""
    if status in ("phone_screen", "interview"):
        return "Send thank-you / follow-up", now + timedelta(days=1)
    if status == "offer":
        return "Review offer", now + timedelta(days=2)
    if status == "applied":
        return "Follow up if no reply", now + timedelta(days=7)
    return None, None


def _occurred_at(email: dict) -> datetime:
    raw = email.get("received_at") or email.get("sent_at")
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _is_enabled(user_id: str) -> bool:
    """Gate: job_search_mode must be explicitly on (fails closed when unset)."""
    row = await db.query_one(
        "SELECT job_search_mode FROM settings WHERE user_id = $1", user_id,
    )
    return bool(row and row.get("job_search_mode"))


async def _load_known(user_id: str) -> dict:
    """Tracked thread_ids + contact emails, so the gate recognises in-flight jobs."""
    rows = await db.query(
        "SELECT thread_id, contact_email FROM job_applications WHERE user_id = $1",
        user_id,
    )
    thread_ids = {r["thread_id"] for r in rows if r.get("thread_id")}
    contacts = {r["contact_email"].lower() for r in rows if r.get("contact_email")}
    return {"thread_ids": thread_ids, "contacts": contacts}
