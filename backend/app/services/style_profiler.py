"""
Writing style profiler — Phase 2.

Analyses sent email history to build a per-user StyleProfile stored in
the settings.style_profile JSONB column.
"""

from app.services.ai_service import ai_service


class StyleProfiler:

    async def build_profile(self, sent_emails: list[dict]) -> dict:
        """
        Analyse up to 200 sent emails and return a style profile dict.
        Stored in settings.style_profile.
        """
        return await ai_service.analyse_writing_style(sent_emails)

    async def update_profile(self, user_id: str, new_emails: list[dict]) -> dict:
        """
        Fetch the user's existing style profile, generate an updated profile
        from new_emails, merge (new wins on conflict), and persist to settings.
        """
        from app import db as _db

        row = await _db.query_one(
            "SELECT style_profile FROM settings WHERE user_id = $1", user_id
        )
        existing: dict = (row or {}).get("style_profile") or {}

        new_profile = await ai_service.analyse_writing_style(new_emails)

        merged = {**existing, **new_profile}

        await _db.upsert(
            "settings",
            {"user_id": user_id, "style_profile": merged},
            conflict_columns=["user_id"],
        )
        return merged


style_profiler = StyleProfiler()
