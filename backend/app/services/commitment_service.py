"""
Commitment Radar service.

Two entry points called from `inbox_sync`:

  scan_inbound(user_id, email)
      Run after triage on an inbound email. Detects promises in either
      direction (sender to user, user to sender) — a commitment block at the
      bottom of an email reply often contains both.

  scan_sent(user_id, sent_email)
      Run after a Gmail-sent message is mirrored locally. Detects promises in
      either direction from the user's own outbound copy.

Both call `ai_service.detect_commitments` (Haiku, JSON-out) and then write to:
  * `commitments` — the canonical row, one per detected promise
  * `contacts.open_commitments` / `their_open_commitments` — denormalised list
    so the UI can show counts on contact pages without joining
  * `memory_episodes` (episode_type='commitment') — so the memory retrieval
    path can surface "you owe Sarah X" inside future drafts to Sarah

Resolution is manual in v1 via `resolve(user_id, commitment_id, status)`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import memory_service
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)


# Confidence below this is stored but not surfaced as a "rescue" item.
SURFACE_CONFIDENCE_FLOOR = 0.7

# Cap on per-contact denormalised list to keep contact reads bounded.
PER_CONTACT_DENORM_CAP = 5


class CommitmentService:

    async def scan_inbound(self, user_id: str, email: dict) -> list[dict]:
        """Detect commitments in an inbound email and persist them.

        Returns the list of saved rows (may be empty).
        """
        return await self._scan(
            user_id=user_id,
            email=email,
            source_kind="inbound",
            source_email_id=email.get("id"),
            counterparty_email=email.get("from_email") or email.get("from") or "",
            counterparty_name=email.get("from_name"),
        )

    async def scan_sent(self, user_id: str, sent_email: dict) -> list[dict]:
        """Detect commitments in a Gmail-sent (or Felix-assisted) email.

        For group threads each detected commitment is routed to the specific
        recipient the model identified (the prompt asks for `counterparty_email`
        per item), so a "Bob, I'll send the deck" line on a To: alice, bob
        message persists under bob — not whichever address came first.
        """
        to_emails: list[str] = list(sent_email.get("to_emails") or [])
        to_names: list[str] = list(sent_email.get("to_names") or [])
        # Legacy single-recipient callers (and the inbound→sent thread mirror).
        if not to_emails:
            legacy = (sent_email.get("to_email") or sent_email.get("to") or "").strip().lower()
            if legacy:
                to_emails = [legacy]
                to_names = [sent_email.get("to_name") or ""]

        if not to_emails:
            return []

        # The prompt's `to_email` field flows the full recipient list so the
        # model can match names in the body to specific addresses.
        rendered_to = ", ".join(to_emails)
        prompt_payload = {**sent_email, "to_email": rendered_to}

        # Default counterparty for the AI call (used by _scan's fallback path
        # when a particular item didn't return a recipient). Pick the first
        # non-automated address.
        default_email, default_name = _primary_counterparty(to_emails, to_names)

        return await self._scan(
            user_id=user_id,
            email=prompt_payload,
            source_kind="sent",
            source_email_id=sent_email.get("id"),
            counterparty_email=default_email,
            counterparty_name=default_name,
            allowed_counterparties=to_emails,
            counterparty_names_by_email={
                addr: (to_names[i] if i < len(to_names) else "")
                for i, addr in enumerate(to_emails)
            },
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _scan(
        self,
        *,
        user_id: str,
        email: dict,
        source_kind: str,
        source_email_id: str | None,
        counterparty_email: str,
        counterparty_name: str | None,
        allowed_counterparties: list[str] | None = None,
        counterparty_names_by_email: dict[str, str] | None = None,
    ) -> list[dict]:
        if not (counterparty_email or "").strip():
            return []

        # Skip noisy automated senders entirely — they almost never make commitments.
        if _looks_automated(counterparty_email):
            return []

        user_email, user_name = await _user_identity(user_id)

        items = await ai_service.detect_commitments(
            email=email,
            source_kind=source_kind,
            user_name=user_name,
            user_email=user_email,
            counterparty_email=counterparty_email,
            counterparty_name=counterparty_name,
            user_id=user_id,
        )
        if not items:
            return []

        # Allow-list of valid recipient addresses for the model's per-item
        # `counterparty_email`. Without this the model could hallucinate an
        # address and we'd silently persist a commitment under it.
        allowed = {a.lower() for a in (allowed_counterparties or []) if a}

        saved: list[dict] = []
        for it in items:
            chosen_email = counterparty_email
            chosen_name = counterparty_name
            item_email = (it.get("counterparty_email") or "").strip().lower()
            if item_email and (not allowed or item_email in allowed):
                chosen_email = item_email
                chosen_name = (counterparty_names_by_email or {}).get(item_email) or chosen_name
            if _looks_automated(chosen_email):
                continue

            row = await self._persist(
                user_id=user_id,
                source_email_id=source_email_id,
                source_kind=source_kind,
                counterparty_email=chosen_email,
                counterparty_name=chosen_name,
                item=it,
            )
            if row:
                saved.append(row)
        return saved

    async def _persist(
        self,
        *,
        user_id: str,
        source_email_id: str | None,
        source_kind: str,
        counterparty_email: str,
        counterparty_name: str | None,
        item: dict,
    ) -> dict | None:
        deadline = _parse_deadline(item.get("deadline_iso"))
        confidence = float(item.get("confidence") or 0.5)
        text = (item.get("text") or "").strip()
        if not text:
            return None

        # Idempotency: if we already have an open row with the same direction +
        # counterparty + near-identical text, skip. (Cheap dedupe — the same
        # email may be re-processed if inbox sync replays.)
        existing = await db.query_one(
            """
            SELECT id FROM commitments
            WHERE user_id = $1
              AND counterparty_email = $2
              AND direction = $3
              AND status = 'open'
              AND LOWER(text) = LOWER($4)
            """,
            user_id, counterparty_email, item["direction"], text,
        )
        if existing:
            return None

        row = await db.insert(
            "commitments",
            {
                "user_id":            user_id,
                "source_email_id":    source_email_id,
                "source_kind":        source_kind,
                "direction":          item["direction"],
                "counterparty_email": counterparty_email,
                "counterparty_name":  counterparty_name,
                "text":               text,
                "source_quote":       (item.get("source_quote") or "")[:200] or None,
                "deadline":           deadline,
                "confidence":         confidence,
                "status":             "open",
            },
        )
        if not row:
            return None

        # Fan-out (best-effort): denormalise to contacts and memory.
        if confidence >= SURFACE_CONFIDENCE_FLOOR:
            asyncio.create_task(_denormalise_to_contact(
                user_id=user_id,
                counterparty_email=counterparty_email,
                direction=item["direction"],
                text=text,
            ))
            asyncio.create_task(_write_commitment_episode(
                user_id=user_id,
                counterparty_email=counterparty_email,
                counterparty_name=counterparty_name,
                direction=item["direction"],
                text=text,
                deadline=deadline,
                source_email_id=source_email_id,
            ))

        return row

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def list_open(
        self,
        user_id: str,
        *,
        direction: str | None = None,
        within_hours: int | None = None,
    ) -> list[dict]:
        """Return open commitments, optionally filtered by direction or deadline window."""
        conditions = ["user_id = $1", "status = 'open'"]
        args: list[Any] = [user_id]
        i = 2
        if direction:
            conditions.append(f"direction = ${i}"); args.append(direction); i += 1
        if within_hours is not None:
            conditions.append(f"deadline IS NOT NULL AND deadline <= NOW() + (${i} || ' hours')::INTERVAL")
            args.append(str(within_hours)); i += 1

        sql = (
            "SELECT * FROM commitments WHERE " + " AND ".join(conditions)
            + " ORDER BY deadline NULLS LAST, created_at DESC"
        )
        return await db.query(sql, *args)

    async def resolve(self, user_id: str, commitment_id: str, status: str = "done") -> dict | None:
        """Mark a commitment as done / dropped / rescued."""
        if status not in {"done", "dropped", "rescued"}:
            raise ValueError(f"unsupported resolution status: {status}")
        row = await db.query_one(
            """
            UPDATE commitments
            SET status = $3, resolved_at = NOW()
            WHERE id = $1 AND user_id = $2
            RETURNING *
            """,
            commitment_id, user_id, status,
        )
        if row:
            asyncio.create_task(_remove_from_contact_denorm(
                user_id=user_id,
                counterparty_email=row.get("counterparty_email") or "",
                direction=row.get("direction") or "",
                text=row.get("text") or "",
            ))
        return row


commitment_service = CommitmentService()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_AUTOMATED_NEEDLES = (
    "noreply", "no-reply", "donotreply", "notifications@", "notification@",
    "invitations@", "alerts@", "mailer-daemon",
)


def _looks_automated(addr: str) -> bool:
    a = (addr or "").lower()
    return any(n in a for n in _AUTOMATED_NEEDLES)


def _primary_counterparty(
    to_emails: list[str], to_names: list[str],
) -> tuple[str, str | None]:
    """First non-automated recipient + matching display name."""
    for i, addr in enumerate(to_emails):
        if addr and not _looks_automated(addr):
            name = to_names[i] if i < len(to_names) else None
            return addr, (name or None)
    if to_emails:
        return to_emails[0], (to_names[0] if to_names else None)
    return "", None


def _parse_deadline(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _user_identity(user_id: str) -> tuple[str, str]:
    settings_row = await db.query_one(
        "SELECT display_name FROM settings WHERE user_id = $1",
        user_id,
    ) or {}
    google_row = await db.query_one(
        "SELECT google_email FROM google_connections WHERE user_id = $1",
        user_id,
    ) or {}
    return google_row.get("google_email") or "", settings_row.get("display_name") or "User"


async def _denormalise_to_contact(
    *,
    user_id: str,
    counterparty_email: str,
    direction: str,
    text: str,
) -> None:
    """Push the commitment text into the contact's open / their_open list (FIFO cap)."""
    column = "open_commitments" if direction == "owed_by_user" else "their_open_commitments"
    try:
        row = await db.query_one(
            f"SELECT {column} AS arr FROM contacts WHERE user_id = $1 AND email = $2",
            user_id, counterparty_email,
        )
        current: list[str] = list((row or {}).get("arr") or [])
        if text in current:
            return
        current.append(text)
        if len(current) > PER_CONTACT_DENORM_CAP:
            current = current[-PER_CONTACT_DENORM_CAP:]
        await db.execute(
            f"UPDATE contacts SET {column} = $1 WHERE user_id = $2 AND email = $3",
            current, user_id, counterparty_email,
        )
    except Exception:
        logger.debug("commitment denorm to contact failed", exc_info=True)


async def _remove_from_contact_denorm(
    *,
    user_id: str,
    counterparty_email: str,
    direction: str,
    text: str,
) -> None:
    if not counterparty_email or not text or direction not in {"owed_by_user", "owed_to_user"}:
        return
    column = "open_commitments" if direction == "owed_by_user" else "their_open_commitments"
    try:
        await db.execute(
            f"UPDATE contacts SET {column} = array_remove({column}, $1) "
            f"WHERE user_id = $2 AND email = $3",
            text, user_id, counterparty_email,
        )
    except Exception:
        logger.debug("commitment denorm removal failed", exc_info=True)


async def _write_commitment_episode(
    *,
    user_id: str,
    counterparty_email: str,
    counterparty_name: str | None,
    direction: str,
    text: str,
    deadline: datetime | None,
    source_email_id: str | None,
) -> None:
    """Emit a Layer-3 episode so future memory retrievals see the commitment."""
    try:
        verb = "owes" if direction == "owed_to_user" else "promised"
        who = counterparty_name or counterparty_email or "someone"
        if direction == "owed_by_user":
            summary = f"You {verb} {who}: {text}"
        else:
            summary = f"{who} {verb} you: {text}"
        if deadline:
            summary += f" (by {deadline.strftime('%Y-%m-%d')})"

        await memory_service.create_episode_with_embedding(
            user_id=user_id,
            episode_type="commitment",
            summary=summary,
            entities=[counterparty_name, counterparty_email] if counterparty_name else [counterparty_email],
            importance=0.7,
            source_type="email",
            source_id=source_email_id,
            occurred_at=datetime.now(timezone.utc),
        )
    except Exception:
        logger.debug("commitment episode write failed", exc_info=True)
