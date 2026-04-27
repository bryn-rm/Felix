"""Digest mode sender job helpers — Phase 7."""

from app import db
from app.middleware.auth import get_google_credentials
from app.services.gmail_service import GmailService
from app.services.polish_service import polish_service


async def send_digest_for_user(user_id: str) -> None:
    settings = await db.query_one(
        "SELECT digest_mode FROM settings WHERE user_id = $1",
        user_id,
    ) or {}
    if not settings.get("digest_mode"):
        return

    digest = await polish_service.build_digest(user_id, window_hours=6)

    creds = await get_google_credentials(user_id)
    gmail = GmailService(creds)

    recipient = await db.query_one(
        "SELECT google_email FROM google_connections WHERE user_id = $1",
        user_id,
    )
    to_email = (recipient or {}).get("google_email")
    if not to_email:
        return

    await gmail.send_email(
        to=to_email,
        subject="Felix Digest",
        body=digest["summary"],
    )


async def send_weekly_review_for_user(user_id: str) -> None:
    recipient = await db.query_one(
        "SELECT google_email FROM google_connections WHERE user_id = $1",
        user_id,
    )
    to_email = (recipient or {}).get("google_email")
    if not to_email:
        return

    review = await polish_service.generate_weekly_review_email(user_id)

    creds = await get_google_credentials(user_id)
    gmail = GmailService(creds)

    await gmail.send_email(
        to=to_email,
        subject=review["subject"],
        body=review["text"],
        html_body=review["html"],
    )
