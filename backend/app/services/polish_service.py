"""
Phase 7 polish service: digest mode, weekly review, templates, style evolution.
"""

from datetime import datetime, timedelta, timezone

from app import db


class PolishService:
    async def build_digest(self, user_id: str, window_hours: int = 6) -> dict:
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        categories = await db.query(
            """
            SELECT category, COUNT(*) AS n
            FROM emails
            WHERE user_id = $1 AND received_at >= $2
            GROUP BY category
            """,
            user_id,
            since,
        )
        counts = {r["category"]: int(r["n"]) for r in categories if r.get("category")}

        pending_drafts = await db.query_one(
            "SELECT COUNT(*) AS n FROM drafts WHERE user_id = $1 AND status = 'pending'",
            user_id,
        )
        overdue_followups = await db.query_one(
            """
            SELECT COUNT(*) AS n
            FROM follow_ups
            WHERE user_id = $1 AND status = 'waiting' AND follow_up_by < NOW()
            """,
            user_id,
        )

        summary = (
            f"In the last {window_hours} hours: "
            f"{counts.get('action_required', 0)} action-required, "
            f"{counts.get('vip', 0)} VIP, "
            f"{counts.get('fyi', 0)} FYI, "
            f"{counts.get('newsletter', 0)} newsletter emails. "
            f"You have {(pending_drafts or {}).get('n', 0)} pending drafts and "
            f"{(overdue_followups or {}).get('n', 0)} overdue follow-ups."
        )

        return {
            "window_hours": window_hours,
            "since": since.isoformat(),
            "counts": counts,
            "pending_drafts": int((pending_drafts or {}).get("n", 0)),
            "overdue_followups": int((overdue_followups or {}).get("n", 0)),
            "summary": summary,
        }

    async def build_weekly_review(self, user_id: str) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=7)

        processed = await db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE user_id = $1 AND received_at >= $2",
            user_id,
            since,
        )
        sent = await db.query_one(
            "SELECT COUNT(*) AS n FROM drafts WHERE user_id = $1 AND status = 'sent' AND sent_at >= $2",
            user_id,
            since,
        )
        followup_closed = await db.query_one(
            "SELECT COUNT(*) AS n FROM follow_ups WHERE user_id = $1 AND status IN ('replied','closed') AND created_at >= $2",
            user_id,
            since,
        )
        top_contacts = await db.query(
            """
            SELECT from_email, COUNT(*) AS n
            FROM emails
            WHERE user_id = $1 AND received_at >= $2
            GROUP BY from_email
            ORDER BY n DESC
            LIMIT 5
            """,
            user_id,
            since,
        )

        summary = (
            f"Weekly review: {int((processed or {}).get('n', 0))} emails processed, "
            f"{int((sent or {}).get('n', 0))} replies sent, and "
            f"{int((followup_closed or {}).get('n', 0))} follow-up items resolved."
        )

        return {
            "since": since.isoformat(),
            "processed_emails": int((processed or {}).get("n", 0)),
            "sent_replies": int((sent or {}).get("n", 0)),
            "resolved_follow_ups": int((followup_closed or {}).get("n", 0)),
            "top_contacts": top_contacts,
            "summary": summary,
        }

    async def suggest_templates(self, user_id: str) -> list[dict]:
        rows = await db.query(
            """
            SELECT
              LOWER(REGEXP_REPLACE(COALESCE(subject, ''), '^re:\\s*', '')) AS subject_key,
              COUNT(*) AS n,
              MIN(COALESCE(edited_text, draft_text)) AS sample
            FROM drafts
            WHERE user_id = $1 AND status IN ('sent', 'approved')
            GROUP BY subject_key
            HAVING COUNT(*) >= 2
            ORDER BY n DESC
            LIMIT 10
            """,
            user_id,
        )
        out = []
        for r in rows:
            key = (r.get("subject_key") or "").strip()
            if not key:
                continue
            out.append(
                {
                    "name": f"Template: {key[:60]}",
                    "subject_key": key,
                    "usage_count": int(r.get("n") or 0),
                    "sample": (r.get("sample") or "")[:500],
                }
            )
        return out

    async def style_evolution_report(self, user_id: str) -> dict:
        recent = await db.query_one(
            """
            SELECT AVG(LENGTH(COALESCE(edited_text, draft_text)))::float AS avg_len
            FROM drafts
            WHERE user_id = $1 AND status = 'sent' AND sent_at >= NOW() - INTERVAL '14 days'
            """,
            user_id,
        )
        prior = await db.query_one(
            """
            SELECT AVG(LENGTH(COALESCE(edited_text, draft_text)))::float AS avg_len
            FROM drafts
            WHERE user_id = $1
              AND status = 'sent'
              AND sent_at >= NOW() - INTERVAL '28 days'
              AND sent_at < NOW() - INTERVAL '14 days'
            """,
            user_id,
        )
        recent_avg = float((recent or {}).get("avg_len") or 0.0)
        prior_avg = float((prior or {}).get("avg_len") or 0.0)
        delta = recent_avg - prior_avg
        trend = "longer" if delta > 20 else "shorter" if delta < -20 else "stable"

        return {
            "recent_avg_chars": recent_avg,
            "prior_avg_chars": prior_avg,
            "delta_chars": delta,
            "trend": trend,
            "summary": f"Your recent sent replies are {trend} compared with the prior two-week period.",
        }


polish_service = PolishService()
