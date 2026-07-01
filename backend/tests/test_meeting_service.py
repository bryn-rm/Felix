"""
Meeting capture orchestration tests — Phase 5.

Covers the fan-out and lifecycle contracts (plan §5 tests d/e/f/h):
  • owner:'me' action item → a source_kind='meeting' commitment carrying
    source_meeting_id; owner:'them' creates nothing.
  • interview → job-tracker fan-out fires only when job_search_mode is on AND a
    tracked job matches; it never blocks or fails the summary.
  • summarize_meeting recovers a meeting from 'error' and lands it 'done'; an AI
    failure leaves it in the recoverable 'error' state.

The DB is faked (SQL routed by table name) so no network/Postgres is needed;
ai_service.summarize_meeting and job_tracker_service.add_event are mocked at the
boundary so these exercise meeting_service's own logic, not their internals.
"""

from unittest.mock import AsyncMock

import pytest

from app.services import meeting_service as ms_mod
from app.services.ai_service import ai_service
from app.services.meeting_service import meeting_service


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------

class FakeDB:
    def __init__(self, *, meeting=None, segments=None, jobs=None,
                 job_search_mode=False, existing_commitment=None):
        self.meeting = meeting
        self.segments = segments or []
        self.jobs = jobs or []
        self.job_search_mode = job_search_mode
        self.existing_commitment = existing_commitment
        self.inserted: list[tuple[str, dict]] = []
        self.executed: list[tuple[str, tuple]] = []

    async def query_one(self, sql, *args):
        s = " ".join(sql.split())
        if "SELECT * FROM meetings" in s:
            return self.meeting
        if "FROM commitments" in s:
            return self.existing_commitment
        if "meeting_capture_mode" in s:
            return {"meeting_capture_mode": True}
        if "job_search_mode" in s:
            return {"job_search_mode": self.job_search_mode}
        if "FROM meeting_summaries" in s:
            return None
        if "FROM job_applications" in s:  # add_event ownership check
            return self.jobs[0] if self.jobs else None
        return None

    async def query(self, sql, *args):
        s = " ".join(sql.split())
        if "FROM meeting_transcript_segments" in s:
            return self.segments
        if "FROM job_applications" in s:
            return self.jobs
        return []

    async def insert(self, table, data):
        self.inserted.append((table, data))
        return {"id": f"{table}-row-1", **data}

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))
        return "OK"

    # convenience accessors
    def inserts(self, table):
        return [d for t, d in self.inserted if t == table]

    def status_updates(self):
        out = []
        for sql, _ in self.executed:
            if "UPDATE meetings SET status = 'done'" in sql:
                out.append("done")
            elif "UPDATE meetings SET status = 'error'" in sql:
                out.append("error")
        return out


def _install_db(monkeypatch, fake: FakeDB):
    for name in ("query_one", "query", "insert", "execute"):
        monkeypatch.setattr(f"app.db.{name}", getattr(fake, name))


def _meeting(**over):
    base = {"id": "m-1", "user_id": "u-1", "template": "general",
            "user_notes": "", "attendees": [], "title": None, "status": "processing"}
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Fan-out: action items → commitments (plan §5 d/e)
# ---------------------------------------------------------------------------

async def test_me_owner_item_creates_meeting_commitment(monkeypatch):
    fake = FakeDB(meeting=_meeting())
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value={
        "tldr": "x",
        "decisions": [],
        "action_items": [
            {"text": "Send the spec", "owner": "me", "due_hint": None},
            {"text": "Review the PR", "owner": "them", "due_hint": None},
        ],
        "enhanced_notes": [],
        "confidence": 0.8,
    }))

    result = await meeting_service.summarize_meeting("u-1", "m-1")

    commits = fake.inserts("commitments")
    assert len(commits) == 1                                  # only owner:'me'
    c = commits[0]
    assert c["source_kind"] == "meeting"
    assert c["source_meeting_id"] == "m-1"
    assert c["direction"] == "owed_by_user"
    assert c["text"] == "Send the spec"
    assert fake.inserts("meeting_summaries")                  # summary persisted
    assert fake.status_updates() == ["done"]
    assert result["confidence"] == 0.8


async def test_them_owner_item_creates_no_commitment(monkeypatch):
    fake = FakeDB(meeting=_meeting())
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value={
        "tldr": "x", "decisions": [],
        "action_items": [{"text": "Their task", "owner": "them", "due_hint": None}],
        "enhanced_notes": [], "confidence": 0.5,
    }))

    await meeting_service.summarize_meeting("u-1", "m-1")

    assert fake.inserts("commitments") == []
    assert fake.status_updates() == ["done"]


async def test_meeting_commitment_due_hint_iso_parses_to_deadline(monkeypatch):
    fake = FakeDB(meeting=_meeting())
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value={
        "tldr": "x", "decisions": [],
        "action_items": [{"text": "Ship it", "owner": "me", "due_hint": "2026-07-01"}],
        "enhanced_notes": [], "confidence": 0.9,
    }))

    await meeting_service.summarize_meeting("u-1", "m-1")

    c = fake.inserts("commitments")[0]
    assert c["deadline"] is not None and c["deadline"].year == 2026


async def test_resummarize_does_not_recreate_resolved_meeting_commitment(monkeypatch):
    fake = FakeDB(
        meeting=_meeting(status="done"),
        existing_commitment={"id": "c-done", "status": "done"},
    )
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value={
        "tldr": "x", "decisions": [],
        "action_items": [{"text": "Send the spec", "owner": "me", "due_hint": None}],
        "enhanced_notes": [], "confidence": 0.9,
    }))

    await meeting_service.summarize_meeting("u-1", "m-1")

    assert fake.inserts("commitments") == []
    assert fake.status_updates() == ["done"]


# ---------------------------------------------------------------------------
# Fan-out: interview → job tracker (plan §5 f)
# ---------------------------------------------------------------------------

def _interview_summary():
    return {
        "tldr": "Strong candidate signal.", "decisions": [],
        "action_items": [], "enhanced_notes": [], "confidence": 0.7,
    }


async def test_interview_fanout_fires_when_mode_on_and_job_matches(monkeypatch):
    job = {"id": "job-1", "company": "Acme", "role_title": "Eng",
           "contact_email": "recruiter@acme.com", "status": "interview"}
    fake = FakeDB(
        meeting=_meeting(template="interview", attendees=["recruiter@acme.com"]),
        jobs=[job], job_search_mode=True,
    )
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value=_interview_summary()))
    add_event = AsyncMock()
    monkeypatch.setattr(
        "app.services.job_tracker_service.job_tracker_service.add_event", add_event
    )

    await meeting_service.summarize_meeting("u-1", "m-1")

    add_event.assert_awaited_once()
    args, kwargs = add_event.call_args
    assert args[0] == "u-1" and args[1] == "job-1" and args[2] == "note"
    assert kwargs["title"] == "Interview notes"
    assert "Strong candidate" in kwargs["detail"]


async def test_interview_fanout_skipped_when_job_mode_off(monkeypatch):
    job = {"id": "job-1", "company": "Acme", "role_title": "Eng",
           "contact_email": "recruiter@acme.com", "status": "interview"}
    fake = FakeDB(
        meeting=_meeting(template="interview", attendees=["recruiter@acme.com"]),
        jobs=[job], job_search_mode=False,
    )
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value=_interview_summary()))
    add_event = AsyncMock()
    monkeypatch.setattr(
        "app.services.job_tracker_service.job_tracker_service.add_event", add_event
    )

    await meeting_service.summarize_meeting("u-1", "m-1")

    add_event.assert_not_awaited()
    assert fake.status_updates() == ["done"]   # summary still succeeds


async def test_interview_fanout_skipped_when_no_job_match(monkeypatch):
    job = {"id": "job-1", "company": "Globex", "role_title": "Eng",
           "contact_email": "hr@globex.com", "status": "interview"}
    fake = FakeDB(
        meeting=_meeting(template="interview", attendees=["someone@else.com"], title="Sync"),
        jobs=[job], job_search_mode=True,
    )
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value=_interview_summary()))
    add_event = AsyncMock()
    monkeypatch.setattr(
        "app.services.job_tracker_service.job_tracker_service.add_event", add_event
    )

    await meeting_service.summarize_meeting("u-1", "m-1")

    add_event.assert_not_awaited()


async def test_interview_match_by_company_in_title(monkeypatch):
    job = {"id": "job-9", "company": "Acme", "role_title": "Eng",
           "contact_email": None, "status": "interview"}
    fake = FakeDB(
        meeting=_meeting(template="interview", attendees=[], title="Interview with Acme Inc"),
        jobs=[job], job_search_mode=True,
    )
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value=_interview_summary()))
    add_event = AsyncMock()
    monkeypatch.setattr(
        "app.services.job_tracker_service.job_tracker_service.add_event", add_event
    )

    await meeting_service.summarize_meeting("u-1", "m-1")

    add_event.assert_awaited_once()
    assert add_event.call_args[0][1] == "job-9"


async def test_interview_fanout_failure_does_not_break_summary(monkeypatch):
    job = {"id": "job-1", "company": "Acme", "role_title": "Eng",
           "contact_email": "recruiter@acme.com", "status": "interview"}
    fake = FakeDB(
        meeting=_meeting(template="interview", attendees=["recruiter@acme.com"]),
        jobs=[job], job_search_mode=True,
    )
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value=_interview_summary()))
    monkeypatch.setattr(
        "app.services.job_tracker_service.job_tracker_service.add_event",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    result = await meeting_service.summarize_meeting("u-1", "m-1")

    assert result is not None                  # fan-out failure swallowed
    assert fake.status_updates() == ["done"]   # meeting stays done, not error


# ---------------------------------------------------------------------------
# Error recovery (plan §5 h)
# ---------------------------------------------------------------------------

async def test_summarize_sets_error_on_ai_failure(monkeypatch):
    fake = FakeDB(meeting=_meeting())
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(side_effect=RuntimeError("api down")))

    result = await meeting_service.summarize_meeting("u-1", "m-1")

    assert result is None
    assert fake.status_updates() == ["error"]
    assert fake.inserts("meeting_summaries") == []


async def test_summarize_recovers_meeting_from_error(monkeypatch):
    """A meeting left in 'error' is re-summarized and lands 'done' (recovery)."""
    fake = FakeDB(meeting=_meeting(status="error"))
    _install_db(monkeypatch, fake)
    monkeypatch.setattr(ai_service, "summarize_meeting", AsyncMock(return_value={
        "tldr": "recovered", "decisions": [], "action_items": [],
        "enhanced_notes": [], "confidence": 0.6,
    }))

    result = await meeting_service.summarize_meeting("u-1", "m-1")

    assert result["tldr"] == "recovered"
    assert fake.status_updates() == ["done"]
    assert fake.inserts("meeting_summaries")


# ---------------------------------------------------------------------------
# start_meeting fail-closed gate
# ---------------------------------------------------------------------------

async def test_start_meeting_refuses_when_capture_disabled(monkeypatch):
    async def disabled_query_one(sql, *args):
        if "meeting_capture_mode" in sql:
            return {"meeting_capture_mode": False}
        return None
    monkeypatch.setattr("app.db.query_one", disabled_query_one)

    with pytest.raises(PermissionError):
        await meeting_service.start_meeting("u-1", template="general")


async def test_start_meeting_creates_recording_row(monkeypatch):
    fake = FakeDB()
    _install_db(monkeypatch, fake)
    # capture enabled via FakeDB.query_one ("meeting_capture_mode" → True)

    result = await meeting_service.start_meeting("u-1", title="Roadmap", template="one_on_one")

    assert result["meeting_id"] == "meetings-row-1"
    row = fake.inserts("meetings")[0]
    assert row["status"] == "recording"
    assert row["source"] == "browser_capture"
    assert row["template"] == "one_on_one"
    assert row["title"] == "Roadmap"
    assert row["started_at"] is not None
