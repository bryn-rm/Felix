"""
Inbox sync — Phase 2.

sync_user_inbox(user_id) is called every 2 minutes per user by the scheduler.
It is completely isolated: exceptions for one user never affect others.

Pipeline for each new email:
  1. Triage via Claude Haiku → category, urgency, sentiment, topic
  2. Persist to emails table (always with user_id)
  3. Apply Felix Gmail labels + mark felix-processed
  4. If action_required or vip → generate draft via Claude Sonnet (streaming,
     collected and stored in drafts table)
  5. Update google_connections.last_sync
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.middleware.auth import get_google_credentials
from app.services import memory_service
from app.services.ai_service import ai_service
from app.services.gmail_service import GmailService
from app.services.relationship_engine import relationship_engine
from app.utils.background import spawn

logger = logging.getLogger(__name__)

# Gmail label names Felix creates in the user's account.
# Nested under "Felix/" so they appear as a tidy group.
CATEGORY_LABEL = {
    "action_required": "Felix/Action Required",
    "fyi":             "Felix/FYI",
    "waiting_on":      "Felix/Waiting On",
    "newsletter":      "Felix/Newsletter",
    "automated":       "Felix/Automated",
    "vip":             "Felix/VIP",
}
# Colours applied to the Gmail labels above, picked from Gmail's fixed label
# palette so they visually match the in-app category badges in EmailCard.tsx.
# Gmail rejects arbitrary hex values — only the documented palette pairs work.
CATEGORY_LABEL_COLOR = {
    "action_required": {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},
    "fyi":             {"backgroundColor": "#cccccc", "textColor": "#000000"},
    "waiting_on":      {"backgroundColor": "#4a86e8", "textColor": "#ffffff"},
    "newsletter":      {"backgroundColor": "#a479e2", "textColor": "#ffffff"},
    "automated":       {"backgroundColor": "#666666", "textColor": "#ffffff"},
    "vip":             {"backgroundColor": "#ffad47", "textColor": "#000000"},
}
PROCESSED_LABEL = "felix-processed"  # flat label used as the sync sentinel


# ---------------------------------------------------------------------------
# Provider quota circuit breaker
#
# Per-email failures are normally swallowed so one malformed email can't stop
# the whole batch. But an Anthropic credit/quota/rate-limit error affects EVERY
# remaining email — retrying each one just floods ai_calls (and the user's
# quota) with calls that are guaranteed to fail. When we detect one, we raise
# ProviderQuotaError to abort this user's batch immediately and let the next
# scheduled sync try again once the provider recovers.
# ---------------------------------------------------------------------------


class ProviderQuotaError(Exception):
    """Signals an exhausted-credit / quota / hard-rate-limit error from the AI
    provider that should stop the current user's sync batch."""


def _is_provider_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "your credit" in text
        or "credit balance" in text
        or "quota" in text
        or "rate limit" in text
        or "rate_limit" in text
    )


# Categories whose mail is never about a specific application, so the job scan
# skips them. ATS confirmations ("we received your application") are sent by
# no-reply senders and get triaged 'automated', so 'automated' must NOT be
# excluded here — that's the single cleanest 'applied' signal and the
# deterministic gate in job_tracker_service already bounds the model cost. Only
# 'newsletter' (job-board digests / marketing) is dropped.
_JOB_SCAN_SKIP_CATEGORIES = {"newsletter"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def sync_user_inbox(user_id: str) -> None:
    """
    Main entry point called by the scheduler every 2 minutes.
    Loads credentials and settings, fetches new emails, processes each one.
    """
    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        # Google not connected or token unrefreshable — skip silently
        logger.warning("Could not load Google credentials for user %s, skipping", user_id)
        return

    user_settings = await db.query_one(
        "SELECT display_name, vip_contacts, style_profile, job_search_mode "
        "FROM settings WHERE user_id = $1",
        user_id,
    )
    if not user_settings:
        logger.warning("No settings row for user %s — skipping inbox sync", user_id)
        return

    gmail = GmailService(creds)
    user_name: str = user_settings.get("display_name") or "User"
    vip_list: list[str] = user_settings.get("vip_contacts") or []
    style_profile: dict = user_settings.get("style_profile") or {}
    # Fails closed: when the flag is off/unset no job detection runs.
    job_search_mode: bool = bool(user_settings.get("job_search_mode"))

    # Snapshot last_sync up front so the sent-mail mirror's "after:" floor
    # can't be poisoned by _touch_last_sync running before the background
    # task gets scheduled.
    last_sync_row = await db.query_one(
        "SELECT last_sync FROM google_connections WHERE user_id = $1",
        user_id,
    )
    last_sync_at = (last_sync_row or {}).get("last_sync")

    # Fetch emails from the last 4 days (read or unread) that haven't been
    # processed yet. The "felix-processed" label prevents double-processing.
    new_emails = await gmail.get_recent_emails(
        max_results=50,
        query="in:inbox newer_than:4d -label:felix-processed",
    )

    if new_emails:
        logger.info("User %s: processing %d new email(s)", user_id, len(new_emails))

        # Initialise a fresh label-ID cache per sync run, per user.  A
        # module-level cache would leak label IDs across users (each user's
        # Gmail account uses different label IDs for the same label name).
        label_cache: dict[str, str] = {}

        for email in new_emails:
            try:
                await _process_email(
                    email=email,
                    user_id=user_id,
                    gmail=gmail,
                    vip_list=vip_list,
                    user_name=user_name,
                    style_profile=style_profile,
                    label_cache=label_cache,
                    job_search_mode=job_search_mode,
                )
            except ProviderQuotaError as exc:
                # Provider is out of credit / rate-limited — every remaining
                # email would fail the same way. Stop the batch; the next
                # scheduled sync retries once the provider recovers.
                logger.error(
                    "Aborting inbox sync batch for user %s — AI provider quota error: %s",
                    user_id, exc,
                )
                break

    # The mirror and catch-up sweeps must run regardless of inbound traffic:
    # a quiet day with only outbound mail still needs sent_emails populated,
    # and earlier failed scans need their retry. Scheduling them outside the
    # `if new_emails` block keeps them firing every cycle.
    #
    # Mirror Gmail "in:sent" so commitment detection + weekly stats see the
    # full picture, not just Felix-assisted drafts. Idempotent — dedupes on
    # (id, user_id) PK in sent_emails.
    spawn(
        _mirror_recent_sent(user_id, gmail, last_sync_at, job_search_mode=job_search_mode),
        name="inbox_sent_mirror",
    )

    # Catch-up sweep: any rows whose commitment scan failed previously will
    # have commitment_scanned_at IS NULL. Retry up to 20 of each per run; the
    # 7-day window stops permanently-bad rows from being retried forever.
    spawn(_catch_up_inbound_commitment_scans(user_id), name="commitment_catchup_inbound")
    spawn(_catch_up_sent_commitment_scans(user_id), name="commitment_catchup_sent")

    # Job-scan retries — only when Job Search Mode is on (fail-closed).
    if job_search_mode:
        spawn(_catch_up_inbound_job_scans(user_id), name="job_catchup_inbound")
        spawn(_catch_up_sent_job_scans(user_id), name="job_catchup_sent")

    await _touch_last_sync(user_id)


# ---------------------------------------------------------------------------
# Per-email pipeline
# ---------------------------------------------------------------------------

async def _process_email(
    *,
    email: dict,
    user_id: str,
    gmail: GmailService,
    vip_list: list[str],
    user_name: str,
    style_profile: dict,
    label_cache: dict[str, str],
    job_search_mode: bool = False,
) -> None:
    """Triage, persist, label, and optionally draft a single email."""
    email_id: str = email["id"]

    try:
        # 1. AI triage — inject the cached profile (Layer 1 only; skip episodic
        # retrieval so per-email triage stays fast and cheap on Haiku).
        triage_memory = await memory_service.build_memory_context(
            user_id=user_id, feature="triage",
        )
        triage = await ai_service.triage_email(
            email, vip_list=vip_list, user_name=user_name,
            user_id=user_id, memory_context=triage_memory,
        )
        category: str = triage.get("category", "fyi")

        # 2. Persist to emails table — upsert on (id, user_id) so re-processing
        #    is safe and just overwrites with fresher triage data.
        await db.upsert(
            "emails",
            {
                "id":                 email_id,
                "user_id":            user_id,
                "thread_id":          email.get("thread_id"),
                "message_id_header":  email.get("message_id_header", ""),
                "from_email":         email.get("from_email", ""),
                "from_name":          email.get("from_name", ""),
                "to_email":           email.get("to", ""),
                "subject":            email.get("subject", ""),
                "body":               email.get("body", ""),
                "snippet":            email.get("snippet", ""),
                "received_at":        email.get("received_at"),
                "category":           category,
                "urgency":            triage.get("urgency"),
                "sentiment":          triage.get("sentiment_of_sender"),
                "topic":              triage.get("topic"),
                "triage_json":        triage,  # asyncpg serialises dict → JSONB
                "processed_at":       datetime.now(timezone.utc),
            },
            conflict_columns=["id", "user_id"],
        )

        # 3. Apply Gmail labels (pass the per-run, per-user cache)
        await _apply_gmail_labels(gmail, email_id, category, label_cache)

        # 4. Auto-draft for emails that need a reply
        if category in ("action_required", "vip"):
            await _generate_and_store_draft(
                email=email,
                user_id=user_id,
                gmail=gmail,
                style_profile=style_profile,
                user_name=user_name,
            )

        # 5. Phase 6 — update relationship profile for the sender (fire-and-forget).
        #    Pass the full email dict so update_contact gets from_email, from_name,
        #    and received_at in one call.  (A second partial call was removed here
        #    because it caused total_emails to be incremented twice per email.)
        spawn(relationship_engine.update_contact(user_id, email), name="relationship_update")

        # 7. Layer 3 — distil interesting emails into episodic memory
        #    (fire-and-forget). The distiller filters by importance, so routine
        #    automated / FYI traffic is silently dropped.
        if category in ("action_required", "vip"):
            spawn(_distil_email_episode(user_id, email, category), name="episode_distil")

        # 6. Phase 5 — auto-close any follow-ups that just got a reply.
        #    If this inbound email is part of a thread we were tracking, mark replied.
        if email.get("thread_id"):
            from app.services.follow_up_engine import follow_up_engine as _fu_engine
            spawn(_fu_engine.mark_replied(user_id, email["thread_id"]), name="followup_mark_replied")

        # 8. Commitment Radar — extract promises in either direction from the
        #    inbound email. Skipped silently for automated/newsletter senders.
        #
        #    We await the scan (rather than fire-and-forget) and stamp
        #    commitment_scanned_at on success. A failure leaves the column
        #    NULL so _catch_up_inbound_commitment_scans on the next sync run
        #    will retry — without this, a transient Anthropic/DB error would
        #    silently drop the commitment forever (the email is already
        #    labeled felix-processed and excluded from the next inbox query).
        if category not in ("automated", "newsletter"):
            try:
                from app.services.commitment_service import commitment_service as _commit
                await _commit.scan_inbound(user_id, {
                    **email,
                    "from_email": email.get("from_email", ""),
                    "from_name":  email.get("from_name", ""),
                })
                await db.execute(
                    "UPDATE emails SET commitment_scanned_at = NOW() "
                    "WHERE id = $1 AND user_id = $2",
                    email_id, user_id,
                )
            except Exception as exc:
                if _is_provider_quota_error(exc):
                    raise ProviderQuotaError(str(exc)) from exc
                logger.warning(
                    "commitment scan failed for email %s; will retry on next sync",
                    email_id, exc_info=True,
                )

        # 9. Job Search Mode — detect application activity. Gated + fail-closed:
        #    skipped entirely unless the user enabled the flag. The service runs
        #    a deterministic pre-filter before any model call (cost control).
        if job_search_mode and category not in _JOB_SCAN_SKIP_CATEGORIES:
            try:
                from app.services.job_tracker_service import job_tracker_service
                await job_tracker_service.scan_email(user_id, {
                    **email,
                    "from_email": email.get("from_email", ""),
                    "from_name":  email.get("from_name", ""),
                })
                # Stamp on success so the catch-up sweep can skip it. A failure
                # leaves job_scanned_at NULL and _catch_up_inbound_job_scans
                # retries — the felix-processed label otherwise excludes this
                # email from the next sync entirely.
                await db.execute(
                    "UPDATE emails SET job_scanned_at = NOW() "
                    "WHERE id = $1 AND user_id = $2",
                    email_id, user_id,
                )
            except Exception as exc:
                if _is_provider_quota_error(exc):
                    raise ProviderQuotaError(str(exc)) from exc
                logger.warning(
                    "job scan failed for email %s; will retry on next sync",
                    email_id, exc_info=True,
                )

    except ProviderQuotaError:
        # Propagate so the batch loop in sync_user_inbox can abort this user.
        raise
    except Exception as exc:
        if _is_provider_quota_error(exc):
            raise ProviderQuotaError(str(exc)) from exc
        logger.exception("Failed to process email %s for user %s", email_id, user_id)
        # Don't re-raise ordinary per-email failures — continue with other emails


async def _apply_gmail_labels(
    gmail: GmailService,
    email_id: str,
    category: str,
    label_cache: dict[str, str],
) -> None:
    """
    Applies the category label (e.g. "Felix/Action Required") and
    the processing sentinel ("felix-processed") in one API call.

    label_cache is a per-run, per-user dict passed in by the caller so label IDs
    are never shared across different users' Gmail accounts.
    """
    label_name = CATEGORY_LABEL.get(category, "Felix/FYI")
    label_color = CATEGORY_LABEL_COLOR.get(category) or CATEGORY_LABEL_COLOR["fyi"]

    label_id, processed_id = await _get_or_create_labels(
        gmail, label_name, label_color, label_cache
    )
    await gmail.apply_labels(email_id, [label_id, processed_id])


async def _get_or_create_labels(
    gmail: GmailService,
    category_label_name: str,
    category_label_color: dict[str, str],
    label_cache: dict[str, str],
) -> tuple[str, str]:
    """Return (category_label_id, processed_label_id), creating them if needed.

    The category label is created/patched with its mapped colour so Gmail's
    label chip matches the in-app badge. The per-run cache ensures we only
    issue the create/patch round-trip once per category per sync run.
    """
    if category_label_name not in label_cache:
        label_cache[category_label_name] = await gmail.get_or_create_label(
            category_label_name, color=category_label_color
        )
    if PROCESSED_LABEL not in label_cache:
        label_cache[PROCESSED_LABEL] = await gmail.get_or_create_label(PROCESSED_LABEL)
    return label_cache[category_label_name], label_cache[PROCESSED_LABEL]


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------

async def _generate_and_store_draft(
    *,
    email: dict,
    user_id: str,
    gmail: GmailService,
    style_profile: dict,
    user_name: str,
) -> None:
    """
    Streams a draft reply from Claude Sonnet, collects the full text,
    and inserts it into the drafts table.

    Skips if a draft already exists for this email (idempotent).
    """
    email_id = email["id"]

    # Idempotency check — only one draft per email
    existing = await db.query_one(
        "SELECT id FROM drafts WHERE email_id = $1 AND user_id = $2",
        email_id, user_id,
    )
    if existing:
        return

    # Fetch thread for context (don't fail if this errors)
    thread_history: list[dict] = []
    if email.get("thread_id"):
        try:
            thread_history = await gmail.get_thread(email["thread_id"])
        except Exception:
            logger.warning("Could not fetch thread %s", email.get("thread_id"))

    # Load contact profile for relationship context
    contact: dict = await db.query_one(
        "SELECT * FROM contacts WHERE email = $1 AND user_id = $2",
        email.get("from_email", ""), user_id,
    ) or {}

    # Build memory context for drafting — include episodic retrieval so the
    # draft can reference prior exchanges / commitments with this sender.
    draft_memory = await memory_service.build_memory_context(
        user_id=user_id,
        feature="draft",
        query=(
            f"{email.get('from_name') or email.get('from_email', '')} "
            f"{email.get('subject', '')}"
        ),
        include_episodes=True,
    )

    # Collect streaming draft
    draft_text = ""
    try:
        draft_metadata: dict = {}
        async for chunk in ai_service.draft_reply(
            email=email,
            thread_history=thread_history,
            contact=contact,
            style_profile=style_profile,
            user_name=user_name,
            user_id=user_id,
            metadata=draft_metadata,
            memory_context=draft_memory,
            quota_scope="background",
        ):
            draft_text += chunk
    except Exception:
        logger.exception("Draft generation failed for email %s user %s", email_id, user_id)
        return

    if not draft_text.strip():
        return

    # Persist draft
    await db.insert(
        "drafts",
        {
            "email_id":     email_id,
            "user_id":      user_id,
            "draft_text":   draft_text,
            "status":       "pending",
        },
    )

    # Flag email row so the frontend can show a draft indicator
    await db.execute(
        "UPDATE emails SET draft_generated = TRUE WHERE id = $1 AND user_id = $2",
        email_id, user_id,
    )

    logger.debug("Draft stored for email %s user %s", email_id, user_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _distil_email_episode(user_id: str, email: dict, category: str) -> None:
    """Distil an important email into a Layer 3 episode (best-effort)."""
    try:
        sender = email.get("from_name") or email.get("from_email") or "someone"
        subject = email.get("subject") or "(no subject)"
        body = (email.get("body") or email.get("snippet") or "")[:4000]
        content = f"From: {sender}\nSubject: {subject}\n\n{body}"
        await memory_service.distil_and_store_episode(
            user_id=user_id,
            episode_type="email",
            content=content,
            source_type="email",
            source_id=email.get("id"),
            occurred_at=email.get("received_at"),
            min_importance=0.4 if category == "vip" else 0.5,
        )
    except Exception:
        logger.debug("episode distil failed for email %s", email.get("id"), exc_info=True)


async def _touch_last_sync(user_id: str) -> None:
    await db.execute(
        "UPDATE google_connections SET last_sync = $1 WHERE user_id = $2",
        datetime.now(timezone.utc), user_id,
    )


async def _mirror_recent_sent(
    user_id: str,
    gmail: GmailService,
    last_sync_at: datetime | None,
    *,
    job_search_mode: bool = False,
) -> None:
    """Mirror Gmail "in:sent" messages into `sent_emails`.

    The query window starts from the last successful sync (minus a small
    overlap) so that >24h downtime doesn't permanently drop sent mail. Capped
    at 7 days back so first-time / stale connections don't backfill the entire
    mailbox. Idempotent: dedupes on (id, user_id) so re-running is safe.

    ``last_sync_at`` is snapshotted by the caller before _touch_last_sync runs,
    so we can't accidentally read the just-bumped value and collapse the
    backfill window to the last 10 minutes.
    """
    from email.utils import getaddresses

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    one_day_ago = now - timedelta(days=1)
    if last_sync_at:
        # Overlap by 10 min so we don't miss anything sent right at the boundary.
        floor = max(seven_days_ago, last_sync_at - timedelta(minutes=10))
    else:
        floor = one_day_ago
    floor_ts = int(floor.timestamp())

    try:
        # Pagination + max_total=500 caps blast in pathological cases; the
        # (id, user_id) PK on sent_emails dedupes overlap with prior runs.
        sent = await gmail.get_recent_emails(
            max_results=100,
            query=f"in:sent after:{floor_ts}",
            paginate=True,
            max_total=500,
        )
    except Exception:
        logger.warning("sent mirror: fetch failed for user %s", user_id, exc_info=True)
        return

    if not sent:
        return

    from app.services.commitment_service import commitment_service

    for msg in sent:
        msg_id = msg.get("id")
        if not msg_id:
            continue

        existing = await db.query_one(
            "SELECT 1 FROM sent_emails WHERE id = $1 AND user_id = $2",
            msg_id, user_id,
        )
        if existing:
            continue

        # Parse the full To + Cc header so group threads (To: a@x, b@y, c@z)
        # preserve every recipient — parseaddr would have collapsed them to
        # the first one, dropping context for everyone else on the thread.
        recipients = getaddresses([msg.get("to") or "", msg.get("cc") or ""])
        to_emails: list[str] = []
        to_names: list[str] = []
        for name, addr in recipients:
            addr = (addr or "").strip().lower()
            if not addr or addr in to_emails:
                continue
            to_emails.append(addr)
            to_names.append((name or "").strip())

        await db.upsert(
            "sent_emails",
            {
                "id":                msg_id,
                "user_id":           user_id,
                "thread_id":         msg.get("thread_id"),
                "message_id_header": msg.get("message_id_header", ""),
                "from_email":        msg.get("from_email", ""),
                "to_emails":         to_emails,
                "to_names":          to_names,
                "subject":           msg.get("subject", ""),
                "body":              msg.get("body", ""),
                "snippet":           msg.get("snippet", ""),
                "sent_at":           msg.get("received_at"),
                "processed_at":      datetime.now(timezone.utc),
            },
            conflict_columns=["id", "user_id"],
        )

        # Commitment scan on outbound — awaited so a failure leaves
        # commitment_scanned_at NULL and the catch-up sweep can retry on the
        # next sync. The PK on sent_emails would otherwise prevent rediscovery.
        try:
            await commitment_service.scan_sent(user_id, {
                **msg,
                "to_emails": to_emails,
                "to_names":  to_names,
            })
            await db.execute(
                "UPDATE sent_emails SET commitment_scanned_at = NOW() "
                "WHERE id = $1 AND user_id = $2",
                msg_id, user_id,
            )
        except Exception:
            logger.warning(
                "commitment scan failed for sent message %s; will retry on next sync",
                msg_id, exc_info=True,
            )

        # Job Search Mode — the user's own outbound application is often the
        # cleanest "applied" signal. Gated + fail-closed; best-effort. Abort the
        # whole mirror on a provider-quota error instead of burning a failed
        # Sonnet call per remaining message (mirrors the inbound batch's abort).
        if job_search_mode:
            if not await _job_scan_one_sent(user_id, msg_id, msg, to_emails, to_names):
                return


async def _job_scan_one_sent(
    user_id: str,
    msg_id: str,
    msg: dict,
    to_emails: list[str],
    to_names: list[str],
) -> bool:
    """Job-scan one mirrored sent message. Returns True to keep mirroring, False
    to abort the mirror loop (provider quota exhausted — every remaining message
    would burn the same failed Sonnet call). Stamps job_scanned_at on success;
    leaves it NULL on a non-quota failure so _catch_up_sent_job_scans retries.
    """
    from app.services.job_tracker_service import job_tracker_service
    try:
        await job_tracker_service.scan_sent(user_id, {
            **msg,
            "to_emails": to_emails,
            "to_names":  to_names,
        })
        await db.execute(
            "UPDATE sent_emails SET job_scanned_at = NOW() "
            "WHERE id = $1 AND user_id = $2",
            msg_id, user_id,
        )
        return True
    except Exception as exc:
        if _is_provider_quota_error(exc):
            logger.error(
                "Aborting sent-mirror job scans for user %s — AI provider quota error: %s",
                user_id, exc,
            )
            return False
        logger.warning(
            "job scan failed for sent message %s; will retry on next sync",
            msg_id, exc_info=True,
        )
        return True


# ---------------------------------------------------------------------------
# Commitment scan catch-up sweeps
# Each scheduled inbox sync queues these to retry rows whose first scan
# attempt failed (commitment_scanned_at IS NULL). Bounded at 20 rows / 7 days
# so they can't run away if every scan keeps failing.
# ---------------------------------------------------------------------------

_CATCH_UP_BATCH = 20
_CATCH_UP_LOOKBACK_DAYS = 7

# Job-scan sweeps drain a larger batch than the commitment sweeps: a provider
# outage can strand a whole sync's worth of job-relevant mail, and a backlog
# bigger than the batch that's drained newest-first would let the oldest rows
# age past the lookback window and be lost forever. The sweeps below order
# oldest-first for the same reason.
_JOB_CATCH_UP_BATCH = 50


async def _catch_up_inbound_commitment_scans(user_id: str) -> None:
    try:
        from app.services.commitment_service import commitment_service as _commit
        rows = await db.query(
            f"""
            SELECT id, from_email, from_name, to_email, subject, body,
                   received_at, category
            FROM emails
            WHERE user_id = $1
              AND commitment_scanned_at IS NULL
              AND received_at > NOW() - INTERVAL '{_CATCH_UP_LOOKBACK_DAYS} days'
              AND COALESCE(category, '') NOT IN ('automated', 'newsletter')
            ORDER BY received_at DESC
            LIMIT {_CATCH_UP_BATCH}
            """,
            user_id,
        )
        for row in rows:
            try:
                await _commit.scan_inbound(user_id, dict(row))
                await db.execute(
                    "UPDATE emails SET commitment_scanned_at = NOW() "
                    "WHERE id = $1 AND user_id = $2",
                    row["id"], user_id,
                )
            except Exception:
                logger.debug(
                    "catch-up inbound scan still failing for %s", row["id"], exc_info=True,
                )
    except Exception:
        logger.exception("inbound commitment catch-up sweep crashed for user %s", user_id)


async def _catch_up_sent_commitment_scans(user_id: str) -> None:
    try:
        from app.services.commitment_service import commitment_service as _commit
        rows = await db.query(
            f"""
            SELECT id, from_email, to_emails, to_names, subject, body, sent_at
            FROM sent_emails
            WHERE user_id = $1
              AND commitment_scanned_at IS NULL
              AND sent_at > NOW() - INTERVAL '{_CATCH_UP_LOOKBACK_DAYS} days'
            ORDER BY sent_at DESC
            LIMIT {_CATCH_UP_BATCH}
            """,
            user_id,
        )
        for row in rows:
            try:
                await _commit.scan_sent(user_id, dict(row))
                await db.execute(
                    "UPDATE sent_emails SET commitment_scanned_at = NOW() "
                    "WHERE id = $1 AND user_id = $2",
                    row["id"], user_id,
                )
            except Exception:
                logger.debug(
                    "catch-up sent scan still failing for %s", row["id"], exc_info=True,
                )
    except Exception:
        logger.exception("sent commitment catch-up sweep crashed for user %s", user_id)


# ---------------------------------------------------------------------------
# Job-scan catch-up sweeps (Job Search Mode)
# Same bounded-retry shape as the commitment sweeps, keyed on job_scanned_at.
# Only spawned when job_search_mode is on (see sync_user_inbox).
# ---------------------------------------------------------------------------


async def _catch_up_inbound_job_scans(user_id: str) -> None:
    try:
        from app.services.job_tracker_service import job_tracker_service
        rows = await db.query(
            f"""
            SELECT id, thread_id, from_email, from_name, subject, body,
                   received_at, category
            FROM emails
            WHERE user_id = $1
              AND job_scanned_at IS NULL
              AND received_at > NOW() - INTERVAL '{_CATCH_UP_LOOKBACK_DAYS} days'
              AND COALESCE(category, '') <> 'newsletter'
            ORDER BY received_at ASC
            LIMIT {_JOB_CATCH_UP_BATCH}
            """,
            user_id,
        )
        for row in rows:
            try:
                await job_tracker_service.scan_email(user_id, dict(row))
                await db.execute(
                    "UPDATE emails SET job_scanned_at = NOW() "
                    "WHERE id = $1 AND user_id = $2",
                    row["id"], user_id,
                )
            except Exception:
                logger.debug(
                    "catch-up inbound job scan still failing for %s", row["id"], exc_info=True,
                )
    except Exception:
        logger.exception("inbound job catch-up sweep crashed for user %s", user_id)


async def _catch_up_sent_job_scans(user_id: str) -> None:
    try:
        from app.services.job_tracker_service import job_tracker_service
        rows = await db.query(
            f"""
            SELECT id, thread_id, from_email, to_emails, to_names, subject, body, sent_at
            FROM sent_emails
            WHERE user_id = $1
              AND job_scanned_at IS NULL
              AND sent_at > NOW() - INTERVAL '{_CATCH_UP_LOOKBACK_DAYS} days'
            ORDER BY sent_at ASC
            LIMIT {_JOB_CATCH_UP_BATCH}
            """,
            user_id,
        )
        for row in rows:
            try:
                await job_tracker_service.scan_sent(user_id, dict(row))
                await db.execute(
                    "UPDATE sent_emails SET job_scanned_at = NOW() "
                    "WHERE id = $1 AND user_id = $2",
                    row["id"], user_id,
                )
            except Exception:
                logger.debug(
                    "catch-up sent job scan still failing for %s", row["id"], exc_info=True,
                )
    except Exception:
        logger.exception("sent job catch-up sweep crashed for user %s", user_id)


# ---------------------------------------------------------------------------
# Style profile refresh (called by weekly scheduler job)
# ---------------------------------------------------------------------------

async def refresh_user_style_profile(user_id: str) -> None:
    """
    Fetch the user's last 200 sent emails, run style analysis via Claude,
    and persist the updated profile to settings.
    """
    try:
        creds = await get_google_credentials(user_id)
    except Exception:
        logger.warning("No Google credentials for user %s — skipping style refresh", user_id)
        return

    gmail = GmailService(creds)
    sent = await gmail.get_sent_emails(max_results=200)
    if not sent:
        logger.info("No sent emails found for user %s", user_id)
        return

    profile = await ai_service.analyse_writing_style(sent, user_id=user_id)
    await db.upsert(
        "settings",
        {"user_id": user_id, "style_profile": profile},
        conflict_columns=["user_id"],
    )
    logger.info("Style profile refreshed for user %s", user_id)
