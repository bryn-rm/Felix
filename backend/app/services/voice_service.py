"""
Voice service — Phase 3.

TTS via ElevenLabs Turbo v2.5, streamed sentence-by-sentence.
generate_and_store() uploads full-audio briefings to Supabase Storage.
"""

import asyncio
import logging
import re
import uuid
from typing import AsyncGenerator

from elevenlabs.client import ElevenLabs

from app import db
from app.config import settings

from supabase import create_client

logger = logging.getLogger(__name__)

# Module-level clients — created once, reused for every request.
# Both ElevenLabs and Supabase clients are thread-safe for concurrent reads.
_el_client = ElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
_supabase_storage = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries, filtering empty strings."""
    parts = _SENTENCE_SPLIT.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _generate_sentence_sync(sentence: str, voice_id: str) -> bytes:
    """
    Generate TTS for a single sentence synchronously.
    Called via asyncio.to_thread so it doesn't block the event loop.
    """
    audio = _el_client.generate(
        text=sentence,
        voice=voice_id,
        model="eleven_turbo_v2_5",
        # stream=False (default) → returns bytes directly
    )
    # audio is bytes when stream=False
    return audio if isinstance(audio, bytes) else b"".join(audio)


def _generate_full_sync(text: str, voice_id: str) -> bytes:
    """Generate full TTS in one call. Used by generate_and_store."""
    audio = _el_client.generate(
        text=text,
        voice=voice_id,
        model="eleven_turbo_v2_5",
    )
    return audio if isinstance(audio, bytes) else b"".join(audio)


class VoiceService:

    async def stream_tts(
        self,
        text: str,
        voice_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream TTS sentence-by-sentence from ElevenLabs.
        Yields raw MP3 bytes for each sentence.

        cancel_event: if set between sentences, generation stops early
        (used for interruption handling).
        """
        vid = voice_id or settings.FELIX_VOICE_ID
        sentences = _split_sentences(text)

        if not sentences:
            return

        for sentence in sentences:
            if cancel_event and cancel_event.is_set():
                logger.debug("TTS cancelled mid-stream (cancel_event set)")
                return
            try:
                audio_bytes = await asyncio.to_thread(_generate_sentence_sync, sentence, vid)
                yield audio_bytes
            except Exception:
                logger.exception("ElevenLabs TTS failed for sentence: %.60s…", sentence)
                return  # don't yield silence; let the caller handle

    async def generate_and_store(self, text: str, user_id: str) -> str:
        """
        Generate full TTS audio, upload to Supabase Storage bucket 'felix-audio',
        and return the public URL.

        Used for morning briefing audio files. Raises on failure.
        """
        voice_id = await self._get_user_voice_id(user_id)

        # Generate full audio in a thread
        audio_bytes = await asyncio.to_thread(_generate_full_sync, text, voice_id)

        # Upload to Supabase Storage (supabase-py is synchronous → run in thread)
        file_path = f"{user_id}/briefing_{uuid.uuid4().hex}.mp3"
        public_url = await asyncio.to_thread(
            self._upload_to_storage, file_path, audio_bytes
        )
        return public_url

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_user_voice_id(self, user_id: str) -> str:
        """Return the user's custom voice ID, falling back to the env default."""
        row = await db.query_one(
            "SELECT felix_voice_id FROM settings WHERE user_id = $1", user_id
        )
        return (row or {}).get("felix_voice_id") or settings.FELIX_VOICE_ID

    @staticmethod
    def _upload_to_storage(file_path: str, audio_bytes: bytes) -> str:
        """Synchronous Supabase Storage upload. Run via asyncio.to_thread.

        Uses the module-level _supabase_storage singleton — no new client per call.
        """
        _supabase_storage.storage.from_("felix-audio").upload(
            path=file_path,
            file=audio_bytes,
            file_options={"content-type": "audio/mpeg"},
        )
        return _supabase_storage.storage.from_("felix-audio").get_public_url(file_path)


voice_service = VoiceService()
