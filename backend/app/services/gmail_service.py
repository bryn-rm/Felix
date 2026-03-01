"""
Gmail API wrapper — Phase 2.

All methods take google.oauth2.credentials.Credentials which are loaded
per-user via get_google_credentials(user_id).
"""

import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build


class GmailService:
    def __init__(self, credentials):
        self.service = build("gmail", "v1", credentials=credentials)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    async def get_recent_emails(self, max_results: int = 50, query: str = "") -> list[dict]:
        """Fetch inbox emails not yet processed by Felix."""
        # TODO Phase 2: implement full fetch + parse pipeline
        results = self.service.users().messages().list(
            userId="me",
            maxResults=max_results,
            q=query or "in:inbox -label:felix-processed",
        ).execute()

        messages = []
        for msg in results.get("messages", []):
            full = self.service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            messages.append(self._parse_message(full))
        return messages

    async def get_sent_emails(self, max_results: int = 200) -> list[dict]:
        """Fetch sent emails — used for style profiling."""
        results = self.service.users().messages().list(
            userId="me", maxResults=max_results, q="in:sent"
        ).execute()
        messages = []
        for msg in results.get("messages", []):
            full = self.service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            messages.append(self._parse_message(full))
        return messages

    async def get_thread(self, thread_id: str) -> list[dict]:
        """Get full thread for context injection."""
        thread = self.service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
        return [self._parse_message(m) for m in thread["messages"]]

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
    ) -> dict:
        """Send an email, optionally as a reply in an existing thread."""
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if thread_id:
            message["References"] = thread_id

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        return self.service.users().messages().send(
            userId="me", body=send_body
        ).execute()

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    async def get_or_create_label(self, name: str) -> str:
        """Return label ID for `name`, creating it if it doesn't exist."""
        labels = self.service.users().labels().list(userId="me").execute()
        for label in labels.get("labels", []):
            if label["name"] == name:
                return label["id"]
        created = self.service.users().labels().create(
            userId="me", body={"name": name}
        ).execute()
        return created["id"]

    async def apply_label(self, message_id: str, label_id: str) -> None:
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_message(self, raw: dict) -> dict:
        headers = {h["name"]: h["value"] for h in raw["payload"]["headers"]}
        body = self._extract_body(raw["payload"])
        return {
            "id": raw["id"],
            "thread_id": raw["threadId"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "snippet": raw.get("snippet", ""),
            "labels": raw.get("labelIds", []),
        }

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract plain-text body from MIME payload."""
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            result = self._extract_body(part)
            if result:
                return result

        return ""
