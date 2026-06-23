"""Tests for Job Search Mode detection + board service.

Covers the plan's verification matrix (a)–(j). A small in-memory FakeDB stands
in for the four tables so multi-step flows (create → reconcile → resolve) are
exercised end to end, while ai_service.detect_job_activity is mocked so no model
call is made.
"""

import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services import job_tracker_service as jt
from app.services.job_tracker_service import (
    apply_status,
    job_tracker_service as svc,
    _looks_job_related,
)


# ---------------------------------------------------------------------------
# In-memory fake DB
# ---------------------------------------------------------------------------


class FakeDB:
    """Minimal asyncpg-shaped stand-in dispatching on known SQL substrings."""

    def __init__(self, *, job_search_mode=True):
        self.jobs: list[dict] = []
        self.events: list[dict] = []
        self.suggestions: list[dict] = []
        self.emails: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.settings = {"job_search_mode": job_search_mode,
                         "display_name": "Test User", "style_profile": {}}
        self._ids = itertools.count(1)

    # -- writes -----------------------------------------------------------
    async def insert(self, table, data):
        row = dict(data)
        row["id"] = f"{table}-{next(self._ids)}"
        {"job_applications": self.jobs,
         "job_events": self.events,
         "job_suggestions": self.suggestions}[table].append(row)
        return row

    async def upsert(self, table, data, conflict_columns=None):
        row = dict(data)
        row.setdefault("id", f"{table}-{next(self._ids)}")
        if table == "drafts":
            self.drafts[data["email_id"]] = row
        return row

    async def execute(self, sql, *args):
        if "status = 'auto_dismissed'" in sql:
            user_id, thread_id, job_id = args
            for s in self.suggestions:
                if (s["user_id"] == user_id and s.get("thread_id") == thread_id
                        and s["status"] == "pending"):
                    s["status"] = "auto_dismissed"
                    s["resolved_at"] = "now"
                    s["proposed_job_id"] = job_id
        elif "UPDATE job_suggestions SET status = $3" in sql:
            sid, user_id, status = args
            for s in self.suggestions:
                if s["id"] == sid and s["user_id"] == user_id:
                    s["status"] = status
                    s["resolved_at"] = "now"
        elif "UPDATE job_applications SET last_activity_at" in sql or \
             "UPDATE job_applications SET next_action = NULL" in sql:
            job_id, user_id = args[0], args[1]
            for j in self.jobs:
                if j["id"] == job_id:
                    if "next_action = NULL" in sql:
                        j["next_action"] = None
                        j["next_action_at"] = None
            return "UPDATE 1"
        elif "UPDATE emails" in sql:
            return "UPDATE 1"
        return "UPDATE 1"

    # -- reads ------------------------------------------------------------
    async def query_one(self, sql, *args):
        if "INSERT INTO job_applications" in sql:
            # Models the partial-UNIQUE ON CONFLICT (user_id, thread_id): a
            # second insert for the same non-null thread returns the existing
            # row instead of creating a duplicate (the duplicate-job race fix).
            (user_id, thread_id, company, role_title, status, contact_name,
             contact_email, applied_at, last_activity_at, next_action,
             next_action_at, confidence) = args
            if thread_id is not None:
                existing = next((j for j in self.jobs if j["user_id"] == user_id
                                 and j.get("thread_id") == thread_id), None)
                if existing:
                    existing["last_activity_at"] = last_activity_at
                    existing["contact_name"] = existing.get("contact_name") or contact_name
                    existing["contact_email"] = existing.get("contact_email") or contact_email
                    return existing
            row = {
                "id": f"job_applications-{next(self._ids)}",
                "user_id": user_id, "thread_id": thread_id, "company": company,
                "role_title": role_title, "status": status, "source": "email",
                "contact_name": contact_name, "contact_email": contact_email,
                "applied_at": applied_at, "last_activity_at": last_activity_at,
                "next_action": next_action, "next_action_at": next_action_at,
                "confidence": confidence,
            }
            self.jobs.append(row)
            return row
        if "FROM drafts WHERE email_id = $1" in sql:
            email_id, _user_id = args
            return self.drafts.get(email_id)
        if "job_search_mode FROM settings" in sql:
            return {"job_search_mode": self.settings["job_search_mode"]}
        if "display_name, style_profile FROM settings" in sql:
            return {"display_name": self.settings["display_name"],
                    "style_profile": self.settings["style_profile"]}
        if "FROM job_applications WHERE user_id = $1 AND thread_id = $2" in sql:
            user_id, thread_id = args
            return next((j for j in self.jobs
                         if j["user_id"] == user_id and j.get("thread_id") == thread_id), None)
        if "FROM job_applications WHERE id = $1 AND user_id = $2" in sql \
                and "SELECT id" not in sql and "UPDATE" not in sql:
            jid, user_id = args
            return next((j for j in self.jobs if j["id"] == jid and j["user_id"] == user_id), None)
        if sql.startswith("\n                UPDATE job_applications") or \
                "UPDATE job_applications\n" in sql:
            return self._apply_job_update(sql, args)
        if "UPDATE job_applications SET" in sql and "RETURNING *" in sql \
                and "\n" not in sql:
            # Manual update() builds a single-line dynamic statement.
            return self._apply_manual_update(sql, args)
        if "LOWER(contact_email) = $2" in sql:
            user_id, recipient = args
            return next((j for j in self.jobs if j["user_id"] == user_id
                         and (j.get("contact_email") or "").lower() == recipient), None)
        if "SELECT id FROM job_events WHERE" in sql:
            user_id, job_id, source_kind, source_id = args
            return next((e for e in self.events
                         if e["user_id"] == user_id and e["job_id"] == job_id
                         and e.get("source_kind") == source_kind
                         and e.get("source_id") == source_id), None)
        if "SELECT id FROM job_suggestions WHERE user_id = $1 AND source_kind" in sql:
            user_id, source_kind, source_id = args
            return next((s for s in self.suggestions
                         if s["user_id"] == user_id and s.get("source_kind") == source_kind
                         and s.get("source_id") == source_id), None)
        if "FROM job_suggestions WHERE id = $1" in sql:
            sid, user_id = args
            return next((s for s in self.suggestions
                         if s["id"] == sid and s["user_id"] == user_id
                         and s["status"] == "pending"), None)
        if "SELECT source_id FROM job_events" in sql:
            user_id, job_id = args
            ins = [e for e in self.events if e["job_id"] == job_id
                   and e["event_type"] == "email_in" and e.get("source_id")]
            return {"source_id": ins[-1]["source_id"]} if ins else None
        if "FROM emails WHERE id = $1" in sql:
            return self.emails.get(args[0])
        if "FROM contacts WHERE" in sql:
            return None
        return None

    async def query(self, sql, *args):
        if "LOWER(contact_email) = $2" in sql:
            user_id, recipient = args
            return [j for j in self.jobs if j["user_id"] == user_id
                    and (j.get("contact_email") or "").lower() == recipient
                    and j.get("status") not in ("rejected", "withdrawn", "accepted")]
        if "SELECT thread_id, contact_email FROM job_applications" in sql:
            return [{"thread_id": j.get("thread_id"), "contact_email": j.get("contact_email")}
                    for j in self.jobs if j["user_id"] == args[0]]
        if "FROM job_applications WHERE user_id = $1 AND status NOT IN" in sql:
            return [j for j in self.jobs if j["user_id"] == args[0]
                    and j["status"] not in ("rejected", "withdrawn", "accepted")]
        if "FROM job_applications WHERE user_id = $1 ORDER BY" in sql:
            return [j for j in self.jobs if j["user_id"] == args[0]]
        if "FROM job_events WHERE user_id = $1 AND job_id" in sql:
            return [e for e in self.events if e["job_id"] == args[1]]
        return []

    def _apply_manual_update(self, sql, args):
        pairs = re.findall(r"(\w+) = \$(\d+)", sql)  # includes id/user_id (harmless)
        jid, user_id = args[0], args[1]
        for j in self.jobs:
            if j["id"] == jid and j["user_id"] == user_id:
                for col, num in pairs:
                    idx = int(num) - 1
                    if idx < len(args):
                        j[col] = args[idx]
                return j
        return None

    def _apply_job_update(self, sql, args):
        (jid, user_id, status, last_activity_at, next_action, next_action_at,
         contact_name, contact_email, thread_id, applied_at) = args
        for j in self.jobs:
            if j["id"] == jid and j["user_id"] == user_id:
                j["status"] = status
                j["last_activity_at"] = last_activity_at
                j["next_action"] = next_action
                j["next_action_at"] = next_action_at
                j["contact_name"] = j.get("contact_name") or contact_name
                j["contact_email"] = j.get("contact_email") or contact_email
                j["thread_id"] = j.get("thread_id") or thread_id
                j["applied_at"] = j.get("applied_at") or applied_at
                return j
        return None


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(jt, "db", db)
    return db


def _mock_detect(monkeypatch, result, calls=None):
    async def _detect(*, email, user_id=None):
        if calls is not None:
            calls.append(email)
        return result(email) if callable(result) else result
    monkeypatch.setattr(jt.ai_service, "detect_job_activity", _detect)


def _email(**kw):
    base = {"id": "e1", "thread_id": "t1", "from_email": "recruiter@greenhouse.io",
            "subject": "Your application", "body": "Thanks for applying.",
            "received_at": "2026-06-01T10:00:00Z"}
    base.update(kw)
    return base


def _detected(**kw):
    base = {"is_job_related": True, "company": "Acme", "role_title": "Backend Engineer",
            "stage": "applied", "contact_name": "Pat", "contact_email": "pat@acme.com",
            "confidence": 0.95, "summary": "Application received"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# (d) deterministic gate — no model call for unrelated mail
# ---------------------------------------------------------------------------


def test_gate_passes_ats_domain():
    assert _looks_job_related(_email(from_email="jobs@greenhouse.io", subject="hi", body="hi"))


def test_gate_passes_keyword():
    assert _looks_job_related(_email(from_email="someone@gmail.com",
                                     subject="Interview invitation", body="Let's schedule"))


def test_gate_rejects_personal_email():
    assert not _looks_job_related(_email(from_email="mum@gmail.com", thread_id="x",
                                         subject="Dinner Sunday?", body="Are you free?"))


@pytest.mark.asyncio
async def test_scan_email_propagates_parse_failure(fake_db, monkeypatch):
    # detect_job_activity raises on malformed model JSON; scan_email must NOT
    # swallow it, so the inbox caller leaves job_scanned_at NULL and retries.
    async def boom(*, email, user_id=None):
        raise ValueError("malformed JSON")

    monkeypatch.setattr(jt.ai_service, "detect_job_activity", boom)
    with pytest.raises(ValueError):
        await svc.scan_email("u1", _email())
    assert fake_db.jobs == []
    assert fake_db.suggestions == []


@pytest.mark.asyncio
async def test_scan_skips_model_when_gate_fails(fake_db, monkeypatch):
    calls = []
    _mock_detect(monkeypatch, _detected(), calls=calls)
    out = await svc.scan_email("u1", _email(from_email="mum@gmail.com",
                                            subject="Dinner?", body="Free Sunday?"))
    assert out is None
    assert calls == []  # detect_job_activity must NOT be invoked


# ---------------------------------------------------------------------------
# (c) fail-closed when job_search_mode is off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_email_noop_when_disabled(monkeypatch):
    db = FakeDB(job_search_mode=False)
    monkeypatch.setattr(jt, "db", db)
    calls = []
    _mock_detect(monkeypatch, _detected(), calls=calls)
    assert await svc.scan_email("u1", _email()) is None
    assert calls == []
    assert db.jobs == []


@pytest.mark.asyncio
async def test_scan_sent_noop_when_disabled(monkeypatch):
    db = FakeDB(job_search_mode=False)
    monkeypatch.setattr(jt, "db", db)
    _mock_detect(monkeypatch, _detected())
    assert await svc.scan_sent("u1", {"id": "s1", "thread_id": "t1",
                                      "to_emails": ["pat@acme.com"], "subject": "Application",
                                      "body": "Applying for the role"}) is None
    assert db.jobs == []


# ---------------------------------------------------------------------------
# (a) high-confidence creates a job + event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_confidence_creates_job_and_event(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(confidence=0.95))
    job = await svc.scan_email("u1", _email())
    assert job is not None
    assert len(fake_db.jobs) == 1
    assert fake_db.jobs[0]["company"] == "Acme"
    assert fake_db.jobs[0]["status"] == "applied"
    assert any(e["event_type"] == "email_in" for e in fake_db.events)


# ---------------------------------------------------------------------------
# (b) low-confidence creates a suggestion, not a job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_creates_suggestion(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(confidence=0.5))
    out = await svc.scan_email("u1", _email())
    assert out is None
    assert fake_db.jobs == []
    assert len(fake_db.suggestions) == 1
    assert fake_db.suggestions[0]["status"] == "pending"
    assert fake_db.suggestions[0]["confidence"] == 0.5  # telemetry kept


# ---------------------------------------------------------------------------
# (e) identity = thread_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_thread_updates_one_job(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(stage="applied"))
    await svc.scan_email("u1", _email(id="e1", thread_id="t1"))
    _mock_detect(monkeypatch, _detected(stage="interview"))
    await svc.scan_email("u1", _email(id="e2", thread_id="t1", subject="Interview"))
    assert len(fake_db.jobs) == 1
    assert fake_db.jobs[0]["status"] == "interview"


@pytest.mark.asyncio
async def test_different_threads_same_company_two_jobs(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(thread="t1", role_title="Backend Engineer"))
    await svc.scan_email("u1", _email(id="e1", thread_id="t1"))
    _mock_detect(monkeypatch, _detected(role_title="Data Scientist"))
    await svc.scan_email("u1", _email(id="e2", thread_id="t2", subject="Your application"))
    assert len(fake_db.jobs) == 2  # same company, different roles → no merge


# ---------------------------------------------------------------------------
# (f) status transition rule
# ---------------------------------------------------------------------------


def test_apply_status_forward_only():
    assert apply_status("applied", "interview") == "interview"
    assert apply_status("offer", "phone_screen") == "offer"  # no regress


def test_apply_status_terminal_unconditional():
    assert apply_status("interview", "rejected") == "rejected"
    assert apply_status("applied", "accepted") == "accepted"
    assert apply_status("rejected", "interview") == "rejected"  # terminal stays


@pytest.mark.asyncio
async def test_rejection_after_interview_via_scan(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(stage="interview"))
    await svc.scan_email("u1", _email(id="e1", thread_id="t1"))
    _mock_detect(monkeypatch, _detected(stage="rejected", summary="Not moving forward"))
    await svc.scan_email("u1", _email(id="e2", thread_id="t1", subject="Update"))
    assert fake_db.jobs[0]["status"] == "rejected"


# ---------------------------------------------------------------------------
# (g) reconciliation: below-floor suggestion then above-floor same-thread email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_auto_dismisses_suggestion(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(confidence=0.5))
    await svc.scan_email("u1", _email(id="e1", thread_id="t1"))
    assert len(fake_db.suggestions) == 1

    _mock_detect(monkeypatch, _detected(confidence=0.95, stage="interview"))
    job = await svc.scan_email("u1", _email(id="e2", thread_id="t1", subject="Interview"))
    assert job is not None
    assert len(fake_db.jobs) == 1
    assert fake_db.suggestions[0]["status"] == "auto_dismissed"


# ---------------------------------------------------------------------------
# (h) outbound scan creates job + email_out event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_sent_creates_job_with_email_out_event(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(stage="applied", confidence=0.9))
    job = await svc.scan_sent("u1", {
        "id": "s1", "thread_id": "t9", "to_emails": ["jobs@lever.co"],
        "subject": "Application for Backend Engineer",
        "body": "Please find my application attached.",
        "received_at": "2026-06-01T09:00:00Z",
    })
    assert job is not None
    assert len(fake_db.jobs) == 1
    assert any(e["event_type"] == "email_out" for e in fake_db.events)


# ---------------------------------------------------------------------------
# (i) resolve_suggestion telemetry + accept creates job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_suggestion_dismiss_keeps_telemetry(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(confidence=0.5))
    await svc.scan_email("u1", _email())
    sug = fake_db.suggestions[0]
    out = await svc.resolve_suggestion("u1", sug["id"], accept=False)
    assert out is None
    assert sug["status"] == "dismissed"
    assert sug["resolved_at"] == "now"
    assert sug["confidence"] == 0.5  # not dropped


@pytest.mark.asyncio
async def test_resolve_suggestion_accept_creates_job(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(confidence=0.5))
    await svc.scan_email("u1", _email())
    sug = fake_db.suggestions[0]
    job = await svc.resolve_suggestion("u1", sug["id"], accept=True)
    assert job is not None
    assert len(fake_db.jobs) == 1
    assert sug["status"] == "accepted"


# ---------------------------------------------------------------------------
# (j) draft_follow_up returns a draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_follow_up_returns_draft(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(stage="interview", confidence=0.95))
    await svc.scan_email("u1", _email(id="e1", thread_id="t1"))
    fake_db.emails["e1"] = {"id": "e1", "user_id": "u1", "from_email": "pat@acme.com",
                            "subject": "Interview", "thread_id": "t1"}

    async def fake_draft_reply(**kwargs):
        for chunk in ["Thank ", "you ", "for ", "your time."]:
            yield chunk

    monkeypatch.setattr(jt.ai_service, "draft_reply", fake_draft_reply)
    job_id = fake_db.jobs[0]["id"]
    result = await svc.draft_follow_up("u1", job_id)
    assert result["draft"] is not None
    assert "Thank you for your time." in result["draft"]["draft_text"]


# ---------------------------------------------------------------------------
# log_outbound_event — preserve a future reminder, clear a due one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_reply_preserves_future_reminder(fake_db):
    future = datetime.now(timezone.utc) + timedelta(days=7)
    fake_db.jobs.append({
        "id": "job-x", "user_id": "u1", "thread_id": "tX", "status": "applied",
        "company": "Acme", "role_title": "Backend Engineer", "contact_email": None,
        "next_action": "Follow up if no reply", "next_action_at": future,
    })
    row = await svc.log_outbound_event("u1", {
        "id": "sent-1", "thread_id": "tX", "to_email": "pat@acme.com",
        "subject": "Re: application", "body": "Quick reply.",
    })
    assert row is not None
    assert row["event_type"] == "email_out"           # not upgraded
    assert fake_db.jobs[0]["next_action_at"] == future  # reminder intact


@pytest.mark.asyncio
async def test_outbound_reply_clears_due_reminder(fake_db):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    fake_db.jobs.append({
        "id": "job-y", "user_id": "u1", "thread_id": "tY", "status": "interview",
        "company": "Acme", "role_title": "Backend Engineer", "contact_email": None,
        "next_action": "Send thank-you / follow-up", "next_action_at": past,
    })
    row = await svc.log_outbound_event("u1", {
        "id": "sent-2", "thread_id": "tY", "to_email": "pat@acme.com",
        "subject": "Thank you", "body": "Thanks for your time.",
    })
    assert row is not None
    assert row["event_type"] == "follow_up_sent"     # upgraded
    assert fake_db.jobs[0]["next_action_at"] is None  # badge cleared


# ---------------------------------------------------------------------------
# update() — recompute follow-up fields on manual stage change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_move_to_terminal_clears_due_badge(fake_db):
    fake_db.jobs.append({
        "id": "job-t", "user_id": "u1", "thread_id": "tT", "status": "applied",
        "company": "Acme", "role_title": "Backend Engineer", "contact_email": None,
        "next_action": "Follow up if no reply",
        "next_action_at": datetime.now(timezone.utc) - timedelta(days=1),
    })
    row = await svc.update("u1", "job-t", {"status": "rejected"})
    assert row["status"] == "rejected"
    assert row["next_action"] is None
    assert row["next_action_at"] is None


@pytest.mark.asyncio
async def test_manual_move_to_interview_sets_reminder(fake_db):
    fake_db.jobs.append({
        "id": "job-i", "user_id": "u1", "thread_id": "tI", "status": "applied",
        "company": "Acme", "role_title": "Backend Engineer", "contact_email": None,
        "next_action": "Follow up if no reply", "next_action_at": None,
    })
    row = await svc.update("u1", "job-i", {"status": "interview"})
    assert row["status"] == "interview"
    assert row["next_action_at"] is not None


@pytest.mark.asyncio
async def test_manual_status_respects_explicit_action(fake_db):
    fake_db.jobs.append({
        "id": "job-e", "user_id": "u1", "thread_id": "tE", "status": "applied",
        "company": "Acme", "role_title": "Backend Engineer", "contact_email": None,
        "next_action": None, "next_action_at": None,
    })
    row = await svc.update("u1", "job-e",
                           {"status": "interview", "next_action": "Custom action"})
    assert row["next_action"] == "Custom action"  # caller value not overwritten


# ---------------------------------------------------------------------------
# Duplicate-job race — concurrent scans on one thread must collapse to one row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_insert_same_thread_no_duplicate(fake_db):
    # Simulate the race: _match_job's read returned None, but a concurrent scan
    # (sent mirror / catch-up sweep) already created the thread's row. Our insert
    # then hits ON CONFLICT (user_id, thread_id) and must collapse onto the
    # existing card instead of creating a duplicate.
    fake_db.jobs.append({
        "id": "job-existing", "user_id": "u1", "thread_id": "t1", "status": "applied",
        "company": "Acme", "role_title": "Backend Engineer", "contact_name": None,
        "contact_email": None, "applied_at": None, "last_activity_at": None,
        "next_action": None, "next_action_at": None,
    })
    job = await svc._upsert_job(
        user_id="u1", match=None, detected=_detected(stage="applied"),
        thread_id="t1", source_id="e2", event_type="email_in",
        occurred_at=datetime.now(timezone.utc),
    )
    assert job["id"] == "job-existing"   # collapsed onto the existing row
    assert len(fake_db.jobs) == 1        # no duplicate card


@pytest.mark.asyncio
async def test_insert_new_thread_creates_job(fake_db):
    # Control: a genuinely new thread still inserts a fresh row.
    job = await svc._upsert_job(
        user_id="u1", match=None, detected=_detected(stage="applied"),
        thread_id="t-new", source_id="e3", event_type="email_in",
        occurred_at=datetime.now(timezone.utc),
    )
    assert job is not None
    assert len(fake_db.jobs) == 1
    assert job["thread_id"] == "t-new"


# ---------------------------------------------------------------------------
# Automated ATS confirmations must still be job-scanned (#2)
# ---------------------------------------------------------------------------


def test_job_scan_includes_automated_excludes_newsletter():
    # ATS "we received your application" mail is triaged 'automated' (no-reply
    # senders), so the job scan must NOT exclude 'automated' — only 'newsletter'.
    from app.jobs.inbox_sync import _JOB_SCAN_SKIP_CATEGORIES

    assert "automated" not in _JOB_SCAN_SKIP_CATEGORIES
    assert "newsletter" in _JOB_SCAN_SKIP_CATEGORIES


@pytest.mark.asyncio
async def test_ats_confirmation_scans_through_gate(fake_db, monkeypatch):
    # A Greenhouse application-received email (the kind triaged 'automated')
    # clears the deterministic gate by ATS domain and reaches detection.
    calls = []
    _mock_detect(monkeypatch, _detected(stage="applied", confidence=0.9), calls=calls)
    job = await svc.scan_email("u1", _email(
        id="ats1", thread_id="tA", from_email="no-reply@us.greenhouse-mail.io",
        subject="Application received", body="Thanks for applying to Acme.",
        category="automated",
    ))
    assert calls != []          # detection ran (gate didn't drop the ATS mail)
    assert job is not None
    assert fake_db.jobs[0]["status"] == "applied"


# ---------------------------------------------------------------------------
# Suggestion upsert conflict is swallowed, not propagated (#8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestion_insert_conflict_is_swallowed(fake_db, monkeypatch):
    import asyncpg

    async def raise_conflict(table, data):
        raise asyncpg.UniqueViolationError("duplicate key value")

    # Pre-check finds nothing (no suggestions yet); the insert races a concurrent
    # one and trips uq_job_suggestions_source. The violation must be swallowed —
    # propagating it would leave job_scanned_at NULL and loop the message forever.
    monkeypatch.setattr(fake_db, "insert", raise_conflict)
    out = await svc._upsert_suggestion(
        user_id="u1", match=None, detected=_detected(confidence=0.5),
        thread_id="t1", source_kind="email", source_id="e1", confidence=0.5,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Outbound attribution across multiple jobs sharing a recruiter (#3 / #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_recipient_ambiguous_not_attributed(fake_db):
    # One recruiter address fronts two of the user's applications. A send on an
    # untracked thread to that address is ambiguous and must NOT be credited to
    # (or clear the badge of) either job.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    for jid in ("job-a", "job-b"):
        fake_db.jobs.append({
            "id": jid, "user_id": "u1", "thread_id": f"th-{jid}", "status": "applied",
            "company": "Acme", "role_title": "Backend Engineer",
            "contact_email": "recruiter@agency.com",
            "next_action": "Follow up if no reply", "next_action_at": past,
        })
    row = await svc.log_outbound_event("u1", {
        "id": "sent-9", "thread_id": "untracked", "to_email": "recruiter@agency.com",
        "subject": "Re: something", "body": "Hi",
    })
    assert row is None                                             # ambiguous → skipped
    assert all(j["next_action_at"] == past for j in fake_db.jobs)  # no badge cleared


@pytest.mark.asyncio
async def test_outbound_recipient_single_match_does_not_clear_badge(fake_db):
    # A single recipient match is attributed, but because it was matched by
    # recipient (not thread) it is logged as plain outbound and must NOT clear a
    # due follow-up reminder (#4 — only a send on the job's own thread does).
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    fake_db.jobs.append({
        "id": "job-solo", "user_id": "u1", "thread_id": "th-solo", "status": "applied",
        "company": "Acme", "role_title": "Backend Engineer",
        "contact_email": "solo@acme.com",
        "next_action": "Follow up if no reply", "next_action_at": past,
    })
    row = await svc.log_outbound_event("u1", {
        "id": "sent-10", "thread_id": "untracked", "to_email": "solo@acme.com",
        "subject": "Re: hi", "body": "Hi",
    })
    assert row is not None
    assert row["event_type"] == "email_out"            # recipient-matched, not upgraded
    assert fake_db.jobs[0]["next_action_at"] == past   # badge intact


# ---------------------------------------------------------------------------
# Sent-mirror job scan aborts the loop on a provider-quota error (#7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sent_mirror_job_scan_aborts_on_quota(monkeypatch):
    from app.jobs import inbox_sync
    from app.services.job_tracker_service import job_tracker_service as jts

    async def boom(user_id, payload):
        raise Exception("rate_limit exceeded for this org")

    monkeypatch.setattr(jts, "scan_sent", boom)
    cont = await inbox_sync._job_scan_one_sent("u1", "s1", {"id": "s1"}, [], [])
    assert cont is False   # abort — don't burn a failed Sonnet call per message


@pytest.mark.asyncio
async def test_sent_mirror_job_scan_continues_on_transient(monkeypatch):
    from app.jobs import inbox_sync
    from app.services.job_tracker_service import job_tracker_service as jts

    async def boom(user_id, payload):
        raise Exception("temporary database hiccup")

    monkeypatch.setattr(jts, "scan_sent", boom)
    cont = await inbox_sync._job_scan_one_sent("u1", "s1", {"id": "s1"}, [], [])
    assert cont is True    # transient failure → keep mirroring, retry next sync


# ---------------------------------------------------------------------------
# draft_follow_up must not clobber an in-progress user-edited draft (#5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_follow_up_preserves_user_edited_draft(fake_db, monkeypatch):
    _mock_detect(monkeypatch, _detected(stage="interview", confidence=0.95))
    await svc.scan_email("u1", _email(id="e1", thread_id="t1"))
    fake_db.emails["e1"] = {"id": "e1", "user_id": "u1", "from_email": "pat@acme.com",
                            "subject": "Interview", "thread_id": "t1"}
    fake_db.drafts["e1"] = {"id": "draft-1", "email_id": "e1", "user_id": "u1",
                            "draft_text": "old draft", "edited_text": "edits in progress",
                            "status": "pending"}

    called = {"n": 0}

    async def fake_draft_reply(**kwargs):
        called["n"] += 1
        yield "regenerated"

    monkeypatch.setattr(jt.ai_service, "draft_reply", fake_draft_reply)
    result = await svc.draft_follow_up("u1", fake_db.jobs[0]["id"])
    assert result["reason"] == "existing_draft_preserved"
    assert result["draft"]["edited_text"] == "edits in progress"   # untouched
    assert called["n"] == 0                                         # no regenerate
    assert fake_db.drafts["e1"]["edited_text"] == "edits in progress"
