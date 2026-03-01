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
        TODO Phase 2: fetch existing profile, merge with new email analysis,
        store updated profile.
        """
        raise NotImplementedError


style_profiler = StyleProfiler()
