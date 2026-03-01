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
from datetime import datetime, timezone

from app import db
from app.middleware.auth import get_google_credentials
from app.services.ai_service import ai_service
from app.services.gmail_service import GmailService
from app.services.relationship_engine import relationship_engine

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
PROCESSED_LABEL = "felix-processed"  # flat label used as the sync sentinel


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
        "SELECT display_name, vip_contacts, style_profile FROM settings WHERE user_id = $1",
        user_id,
    )
    if not user_settings:
        logger.warning("No settings row for user %s — skipping inbox sync", user_id)
        return

    gmail = GmailService(creds)
    user_name: str = user_settings.get("display_name") or "User"
    vip_list: list[str] = user_settings.get("vip_contacts") or []
    style_profile: dict = user_settings.get("style_profile") or {}

    # Fetch only emails not yet processed. Gmail label "felix-processed"
    # is applied after each email is handled, so this query is idempotent.
    new_emails = await gmail.get_recent_emails(
        max_results=50,
        query="in:inbox is:unread -label:felix-processed",
    )

    if not new_emails:
        await _touch_last_sync(user_id)
        return

    logger.info("User %s: processing %d new email(s)", user_id, len(new_emails))

    for email in new_emails:
        await _process_email(
            email=email,
            user_id=user_id,
            gmail=gmail,
            vip_list=vip_list,
            user_name=user_name,
            style_profile=style_profile,
        )

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
) -> None:
    """Triage, persist, label, and optionally draft a single email."""
    email_id: str = email["id"]

    try:
        # 1. AI triage
        triage = await ai_service.triage_email(
            email, vip_list=vip_list, user_name=user_name
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

        # 3. Apply Gmail labels
        await _apply_gmail_labels(gmail, email_id, category)

        # 3.5 Incrementally refresh relationship profile for this sender.
        try:
            await relationship_engine.update_contact(user_id, {
                "from_email": email.get("from_email", ""),
                "from_name": email.get("from_name", ""),
                "topic": triage.get("topic"),
                "received_at": email.get("received_at"),
            })
        except Exception:
            logger.exception("Relationship update failed for sender %s user %s", email.get("from_email"), user_id)

        # 4. Auto-draft for emails that need a reply
        if category in ("action_required", "vip"):
            await _generate_and_store_draft(
                email=email,
                user_id=user_id,
                gmail=gmail,
                style_profile=style_profile,
                user_name=user_name,
            )

    except Exception:
        logger.exception("Failed to process email %s for user %s", email_id, user_id)
        # Don't re-raise — we still want to continue with other emails


async def _apply_gmail_labels(gmail: GmailService, email_id: str, category: str) -> None:
    """
    Applies the category label (e.g. "Felix/Action Required") and
    the processing sentinel ("felix-processed") in one API call.
    """
    label_name = CATEGORY_LABEL.get(category, "Felix/FYI")

    label_id, processed_id = await _get_or_create_labels(gmail, label_name)
    await gmail.apply_labels(email_id, [label_id, processed_id])


# Cache label IDs within a sync run to avoid redundant API calls
_label_cache: dict[str, str] = {}


async def _get_or_create_labels(gmail: GmailService, category_label_name: str) -> tuple[str, str]:
    """Return (category_label_id, processed_label_id), creating them if needed."""
    if category_label_name not in _label_cache:
        _label_cache[category_label_name] = await gmail.get_or_create_label(category_label_name)
    if PROCESSED_LABEL not in _label_cache:
        _label_cache[PROCESSED_LABEL] = await gmail.get_or_create_label(PROCESSED_LABEL)
    return _label_cache[category_label_name], _label_cache[PROCESSED_LABEL]


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

    # Collect streaming draft
    draft_text = ""
    try:
        async for chunk in ai_service.draft_reply(
            email=email,
            thread_history=thread_history,
            contact=contact,
            style_profile=style_profile,
            user_name=user_name,
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

async def _touch_last_sync(user_id: str) -> None:
    await db.execute(
        "UPDATE google_connections SET last_sync = $1 WHERE user_id = $2",
        datetime.now(timezone.utc), user_id,
    )


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

    profile = await ai_service.analyse_writing_style(sent)
    await db.upsert(
        "settings",
        {"user_id": user_id, "style_profile": profile},
        conflict_columns=["user_id"],
    )
    logger.info("Style profile refreshed for user %s", user_id)
