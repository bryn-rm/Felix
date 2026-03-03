"""
Gmail API wrapper.

All blocking Google API calls (execute()) are run via asyncio.to_thread() so
they never block the event loop. Every method takes a Credentials object
loaded per-user via get_google_credentials(user_id) — never a singleton.
"""

import asyncio
import base64
import logging
import re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.google_api import execute_with_backoff as _execute_with_backoff

logger = logging.getLogger(__name__)


class GmailService:
    def __init__(self, credentials):
        # build() reads the discovery doc; cache_discovery avoids the network hit
        # on subsequent instantiations within the same process.
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=True)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    async def get_recent_emails(self, max_results: int = 50, query: str = "") -> list[dict]:
        """
        Fetch inbox emails matching the query. Default query excludes emails
        already labelled felix-processed to avoid double-processing.
        """
        q = query or "in:inbox is:unread -label:felix-processed"

        request = self.service.users().messages().list(
            userId="me", maxResults=max_results, q=q
        )
        try:
            results = await _execute_with_backoff(request, context="list inbox messages")
        except HttpError as e:
            _handle_http_error(e, context="list inbox messages")
            return []

        messages = []
        for msg in results.get("messages", []):
            try:
                full = await self._fetch_full_message(msg["id"])
                messages.append(self._parse_message(full))
            except HttpError as e:
                logger.warning("Could not fetch message %s: %s", msg["id"], e)

        return messages

    async def get_sent_emails(self, max_results: int = 200) -> list[dict]:
        """Fetch sent emails for writing style analysis."""
        request = self.service.users().messages().list(
            userId="me", maxResults=max_results, q="in:sent"
        )
        try:
            results = await _execute_with_backoff(request, context="list sent messages")
        except HttpError as e:
            _handle_http_error(e, context="list sent messages")
            return []

        messages = []
        for msg in results.get("messages", []):
            try:
                full = await self._fetch_full_message(msg["id"])
                messages.append(self._parse_message(full))
            except HttpError as e:
                logger.warning("Could not fetch sent message %s: %s", msg["id"], e)

        return messages

    async def get_message(self, message_id: str) -> dict:
        """Fetch a single message by its Gmail message ID."""
        try:
            full = await self._fetch_full_message(message_id)
        except HttpError as e:
            _handle_http_error(e, context=f"get message {message_id}")
            raise
        return self._parse_message(full)

    async def get_thread(self, thread_id: str) -> list[dict]:
        """
        Return all messages in a thread, oldest first.
        Used to inject conversation history into draft prompts.
        """
        request = self.service.users().threads().get(
            userId="me", id=thread_id, format="full"
        )
        try:
            thread = await _execute_with_backoff(request, context=f"get thread {thread_id}")
        except HttpError as e:
            _handle_http_error(e, context=f"get thread {thread_id}")
            raise
        return [self._parse_message(m) for m in thread.get("messages", [])]

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
    ) -> dict:
        """Send a standalone (non-reply) email."""
        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject
        return await self._send_raw(message)

    async def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str,
        original_message_id: str,  # the RFC 2822 Message-ID header value
    ) -> dict:
        """
        Send a reply in an existing Gmail thread.

        Sets In-Reply-To and References headers so Gmail threads correctly,
        and passes threadId so the API places it in the right thread.
        """
        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        message["In-Reply-To"] = original_message_id
        message["References"] = original_message_id
        return await self._send_raw(message, thread_id=thread_id)

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    async def get_or_create_label(self, name: str) -> str:
        """
        Return the label ID for `name`, creating it if it doesn't exist.
        Uses a nested path for readability: e.g. "Felix/Action Required".
        """
        request = self.service.users().labels().list(userId="me")
        try:
            result = await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context=f"list labels (looking for '{name}')")
            raise

        for label in result.get("labels", []):
            if label["name"].lower() == name.lower():
                return label["id"]

        create_request = self.service.users().labels().create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        try:
            created = await asyncio.to_thread(create_request.execute)
        except HttpError as e:
            _handle_http_error(e, context=f"create label '{name}'")
            raise
        return created["id"]

    async def apply_labels(self, message_id: str, label_ids: list[str]) -> None:
        """Add one or more labels to a message in a single API call."""
        request = self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": label_ids},
        )
        try:
            await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context=f"apply labels to message {message_id}")
            raise

    async def remove_labels(self, message_id: str, label_ids: list[str]) -> None:
        """Remove one or more labels from a message."""
        request = self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": label_ids},
        )
        try:
            await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context=f"remove labels from message {message_id}")
            raise

    async def mark_read(self, message_id: str) -> None:
        """Remove the UNREAD label from a message."""
        await self.remove_labels(message_id, ["UNREAD"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_full_message(self, message_id: str) -> dict:
        request = self.service.users().messages().get(
            userId="me", id=message_id, format="full"
        )
        try:
            return await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context=f"fetch message {message_id}")
            raise

    async def _send_raw(self, message: MIMEText | MIMEMultipart, thread_id: str | None = None) -> dict:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        request = self.service.users().messages().send(userId="me", body=body)
        try:
            return await asyncio.to_thread(request.execute)
        except HttpError as e:
            _handle_http_error(e, context="send message")
            raise

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_message(self, raw: dict) -> dict:
        """
        Convert a Gmail API full message object into a flat dict.

        Extracts:
        - Standard headers (From, To, Subject, Date, Message-ID)
        - Plain-text body (HTML stripped as fallback)
        - received_at from internalDate (reliable; Date header can be forged/malformed)
        - from_email / from_name split from the From header
        """
        payload = raw.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

        from_raw = headers.get("from", "")
        from_name, from_email = parseaddr(from_raw)

        # internalDate is milliseconds since epoch — more reliable than the Date header
        internal_date_ms = int(raw.get("internalDate", 0))
        received_at = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)

        body = self._extract_body(payload)

        return {
            "id": raw["id"],
            "thread_id": raw.get("threadId"),
            "message_id_header": headers.get("message-id", ""),  # RFC 2822 Message-ID
            "from": from_raw,
            "from_email": from_email.lower(),
            "from_name": from_name,
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "received_at": received_at,
            "body": body[:50_000],  # cap to avoid storing extremely large bodies
            "snippet": raw.get("snippet", ""),
            "labels": raw.get("labelIds", []),
        }

    def _extract_body(self, payload: dict) -> str:
        """
        Recursively walk the MIME payload tree.
        Prefers text/plain; falls back to text/html (stripped of tags).
        """
        mime_type = payload.get("mimeType", "")

        # Leaf node — text/plain is exactly what we want
        if mime_type == "text/plain":
            return self._decode_part(payload)

        # Leaf node — HTML: decode and strip tags
        if mime_type == "text/html":
            html = self._decode_part(payload)
            return _strip_html(html)

        # multipart/* — recurse, prefer the plain-text part
        parts = payload.get("parts", [])
        plain = next((p for p in parts if p.get("mimeType") == "text/plain"), None)
        if plain:
            result = self._extract_body(plain)
            if result:
                return result

        # Fall back: try every part in order
        for part in parts:
            result = self._extract_body(part)
            if result:
                return result

        return ""

    def _decode_part(self, part: dict) -> str:
        data = part.get("body", {}).get("data", "")
        if not data:
            return ""
        # Gmail uses URL-safe base64 without padding; add padding to be safe
        padded = data + "=" * (4 - len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _handle_http_error(error: HttpError, context: str = "") -> None:
    """
    Log structured warnings for common Gmail API HttpError codes.

    401 — access token expired / invalid (caller should re-auth).
    403 — insufficient OAuth scope or quota exceeded.
    429 — rate limit hit (back off).

    We log and let the caller re-raise so the background job can skip
    this user gracefully without crashing the whole sync run.
    """
    code = error.resp.status if hasattr(error, "resp") else None
    prefix = f"Gmail API error [{context}]" if context else "Gmail API error"
    if code == 401:
        logger.warning("%s: 401 Unauthorized — token expired or revoked. User must re-connect.", prefix)
    elif code == 403:
        logger.warning("%s: 403 Forbidden — insufficient scope or quota. Check OAuth consent.", prefix)
    elif code == 429:
        logger.warning("%s: 429 Too Many Requests — rate limit hit. Backing off.", prefix)
    else:
        logger.warning("%s: HTTP %s — %s", prefix, code, error)


def _strip_html(html: str) -> str:
    """Very lightweight HTML stripper — removes tags and collapses whitespace."""
    # Remove <style> and <script> blocks entirely
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()
