"""
Meeting capture orchestration — Phase 5 of the meeting-capture feature.

Lifecycle (start → notes → end → summarize) plus the reuse-based fan-out:
  • action items the user owns become commitments (via the existing
    commitment_service + follow-up engine — no new reminder logic);
  • interview meetings, when job-search mode is on, attach a note to the
    matching tracked job.

Heavy work (summarization + fan-out) runs off the request path via `spawn`
(see `end_meeting`). Mirrors the `commitment_service` / `job_tracker_service`
module-singleton shape.

The transcript itself is written by `meeting_stt_service` (Phase 2); this
service only reads finalized segments back, ordered by meeting-relative
`ts_start`, to assemble the summarizer input.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.config import settings
from app.middleware.auth import get_google_credentials
from app.services.ai_service import ai_service
from app.services.calendar_service import CalendarService
from app.services.commitment_service import _parse_deadline, commitment_service
from app.utils.background import spawn

logger = logging.getLogger(__name__)


class MeetingService:

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_meeting(
        self,
        user_id: str,
        *,
        calendar_event_id: str | None = None,
        title: str | None = None,
        template: str = "general",
    ) -> dict:
        """Create a `recording` meeting row and return its id.

        Fail-closed: raises `PermissionError` when meeting capture is off for
        the user. When linked to a calendar event, pulls its title/attendees for
        context (best-effort — never blocks start).
        """
        if not await _capture_enabled(user_id):
            raise PermissionError("meeting capture is disabled")

        now = datetime.now(timezone.utc)
        resolved_title = title
        attendees: list[str] = []
        if calendar_event_id:
            event = await _fetch_calendar_event(user_id, calendar_event_id)
            if event:
                resolved_title = resolved_title or event.get("title")
                attendees = event.get("attendees") or []

        row = await db.insert(
            "meetings",
            {
                "user_id":           user_id,
                "calendar_event_id": calendar_event_id,
                "title":             resolved_title,
                "attendees":         attendees,
                "date":              now,
                "template":          template,
                "status":            "recording",
                "source":            "browser_capture",
                "started_at":        now,
                "updated_at":        now,
            },
        )
        return {"meeting_id": str(row["id"])}

    async def save_user_notes(self, user_id: str, meeting_id: str, content: str) -> None:
        """Persist the user's live notes (debounced upsert from the frontend)."""
        await db.execute(
            "UPDATE meetings SET user_notes = $3, updated_at = NOW() "
            "WHERE id = $1 AND user_id = $2",
            meeting_id, user_id, content,
        )

    async def end_meeting(self, user_id: str, meeting_id: str) -> dict | None:
        """Stop recording and kick off summarization in the background.

        Guarded to `status='recording'` so a duplicate `/end` (or the auto-end
        sweep racing the client) transitions — and spawns the summarizer — only
        once. Returns None if the meeting wasn't owned/recording.
        """
        row = await db.query_one(
            """
            UPDATE meetings
            SET ended_at = NOW(), status = 'processing', updated_at = NOW()
            WHERE id = $1 AND user_id = $2 AND status = 'recording'
            RETURNING id
            """,
            meeting_id, user_id,
        )
        if not row:
            return None
        spawn(self.summarize_meeting(user_id, meeting_id), name="meeting_summarize")
        return {"meeting_id": str(row["id"]), "status": "processing"}

    async def summarize_meeting(self, user_id: str, meeting_id: str) -> dict | None:
        """Assemble transcript + notes, summarize, persist, and fan out.

        On any failure the meeting is left in `status='error'`, which is
        recoverable: re-invoking this method (via `POST /meetings/{id}/summarize`)
        retries the whole path. The per-meeting commitment dedupe makes the retry
        idempotent.
        """
        meeting = await db.query_one(
            "SELECT * FROM meetings WHERE id = $1 AND user_id = $2",
            meeting_id, user_id,
        )
        if not meeting:
            return None

        try:
            segments = await db.query(
                "SELECT speaker, text, ts_start FROM meeting_transcript_segments "
                "WHERE user_id = $1 AND meeting_id = $2 ORDER BY ts_start",
                user_id, meeting_id,
            )
            transcript = "\n".join(f"{s['speaker']}: {s['text']}" for s in segments)
            user_notes = meeting.get("user_notes") or ""
            template = meeting.get("template") or "general"
            md = meeting.get("started_at") or meeting.get("date")
            meeting_date = md.isoformat() if hasattr(md, "isoformat") else (str(md) if md else None)

            summary = await ai_service.summarize_meeting(
                transcript=transcript,
                user_notes=user_notes,
                template=template,
                meeting_date=meeting_date,
                user_id=user_id,
            )
            if summary.get("parse_error"):
                # Unparseable model output — fail into 'error' (recoverable via
                # /summarize) instead of persisting a garbage summary as 'done'.
                raise ValueError("meeting summary response was not valid JSON")

            await db.insert(
                "meeting_summaries",
                {
                    "user_id":        user_id,
                    "meeting_id":     meeting_id,
                    "tldr":           summary.get("tldr"),
                    "decisions":      summary.get("decisions") or [],
                    "action_items":   summary.get("action_items") or [],
                    "enhanced_notes": summary.get("enhanced_notes") or [],
                    "model":          settings.ANTHROPIC_MODEL_SMART,
                    "confidence":     summary.get("confidence"),
                },
            )
            await db.execute(
                "UPDATE meetings SET status = 'done', updated_at = NOW() "
                "WHERE id = $1 AND user_id = $2",
                meeting_id, user_id,
            )
        except Exception:
            logger.exception("summarize_meeting failed for meeting %s", meeting_id)
            await db.execute(
                "UPDATE meetings SET status = 'error', updated_at = NOW() "
                "WHERE id = $1 AND user_id = $2",
                meeting_id, user_id,
            )
            return None

        # Fan-out runs after the summary is committed and the meeting is 'done'.
        # A fan-out failure must NOT flip the meeting back to error — the summary
        # already succeeded. Each branch is independently guarded.
        await self._fan_out(user_id, meeting, summary)
        return summary

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_meetings(self, user_id: str) -> list[dict]:
        return await db.query(
            "SELECT * FROM meetings WHERE user_id = $1 "
            "ORDER BY started_at DESC NULLS LAST, created_at DESC",
            user_id,
        )

    async def get_meeting(self, user_id: str, meeting_id: str) -> dict | None:
        """Return the meeting plus its segments and latest summary."""
        meeting = await db.query_one(
            "SELECT * FROM meetings WHERE id = $1 AND user_id = $2",
            meeting_id, user_id,
        )
        if not meeting:
            return None
        segments = await db.query(
            "SELECT * FROM meeting_transcript_segments "
            "WHERE user_id = $1 AND meeting_id = $2 ORDER BY ts_start",
            user_id, meeting_id,
        )
        summary = await db.query_one(
            "SELECT * FROM meeting_summaries "
            "WHERE user_id = $1 AND meeting_id = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            user_id, meeting_id,
        )
        return {"meeting": meeting, "segments": segments, "summary": summary}

    # ------------------------------------------------------------------
    # Fan-out (reuse — see §2.6)
    # ------------------------------------------------------------------

    async def _fan_out(self, user_id: str, meeting: dict, summary: dict) -> None:
        meeting_id = str(meeting["id"])

        # 1. Action items the user owns → commitments (owed_by_user).
        for item in summary.get("action_items") or []:
            if (item.get("owner") or "").strip().lower() != "me":
                continue  # owner 'them'/<name> → not the user's commitment
            text = (item.get("text") or "").strip()
            if not text:
                continue
            try:
                await commitment_service.create_from_meeting(
                    user_id,
                    text=text,
                    # due_iso is the model-resolved absolute date; due_hint is
                    # natural language _parse_deadline can't read, so it's only a
                    # last-ditch fallback (e.g. if the model already emitted ISO there).
                    deadline=_parse_deadline(item.get("due_iso") or item.get("due_hint")),
                    meeting_id=meeting_id,
                )
            except Exception:
                logger.warning(
                    "create_from_meeting failed for meeting %s", meeting_id, exc_info=True
                )

        # 2. Interview meetings → job tracker (guarded, best-effort).
        if (meeting.get("template") or "") == "interview":
            try:
                await self._link_interview_to_job(user_id, meeting, summary)
            except Exception:
                logger.warning(
                    "interview→job fan-out failed for meeting %s", meeting_id, exc_info=True
                )

    async def _link_interview_to_job(
        self, user_id: str, meeting: dict, summary: dict,
    ) -> None:
        # Lazy import: keeps the job tracker (and its deps) off the hot import
        # path and out of any import cycle.
        from app.services.job_tracker_service import _is_enabled, job_tracker_service

        if not await _is_enabled(user_id):
            return
        job = await _match_job_for_meeting(user_id, meeting)
        if not job:
            return
        await job_tracker_service.add_event(
            user_id,
            str(job["id"]),
            "note",
            title="Interview notes",
            detail=summary.get("tldr") or "",
            # Dedupe per meeting so an error-recovery re-summarize (allowed from
            # 'done') doesn't append a duplicate timeline event each run.
            source_kind="meeting",
            source_id=str(meeting["id"]),
        )


meeting_service = MeetingService()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _capture_enabled(user_id: str) -> bool:
    """Gate: meeting_capture_mode must be explicitly on (fails closed when unset)."""
    row = await db.query_one(
        "SELECT meeting_capture_mode FROM settings WHERE user_id = $1", user_id,
    )
    return bool(row and row.get("meeting_capture_mode"))


async def _fetch_calendar_event(user_id: str, event_id: str) -> dict | None:
    """Best-effort fetch of a linked calendar event for title/attendees context.

    There's no single-event getter on CalendarService, so we pull a window
    around now and match by id (the same pattern meeting_prep_service uses).
    """
    try:
        creds = await get_google_credentials(user_id)
        cal = CalendarService(creds)
        now = datetime.now(timezone.utc)
        events = await cal.get_events(
            time_min=(now - timedelta(hours=2)).isoformat(),
            time_max=(now + timedelta(hours=12)).isoformat(),
        )
        for event in events:
            if event.get("id") == event_id:
                return event
    except Exception:
        logger.info("calendar event fetch failed for user %s", user_id, exc_info=True)
    return None


async def _match_job_for_meeting(user_id: str, meeting: dict) -> dict | None:
    """Match a tracked job to an interview meeting by attendee email, then by
    company name appearing in the meeting title. Conservative — attendee email
    is the strong signal; company-in-title is a fallback."""
    from app.services.job_tracker_service import _normalize_company

    jobs = await db.query(
        "SELECT * FROM job_applications WHERE user_id = $1 "
        "AND status NOT IN ('rejected','withdrawn','accepted')",
        user_id,
    )
    if not jobs:
        return None

    attendees = {a.lower() for a in (meeting.get("attendees") or []) if a}
    for job in jobs:
        contact = (job.get("contact_email") or "").lower()
        if contact and contact in attendees:
            return job

    norm_title = _normalize_company(meeting.get("title") or "")
    if norm_title:
        for job in jobs:
            company = _normalize_company(job.get("company") or "")
            if company and company in norm_title:
                return job
    return None
