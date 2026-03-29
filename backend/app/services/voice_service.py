"""
Voice service — Phase 3.

TTS via ElevenLabs HTTP streaming endpoint:
  - eleven_flash_v2_5 for voice command responses (speed first)
  - eleven_v3         for briefing audio          (quality first)

Use the WebSocket TTS endpoint only if text is being streamed in chunks from
Claude simultaneously; here text is always available upfront so the HTTP
streaming endpoint gives lower latency.

generate_and_store() uploads full-audio briefings to Supabase Storage.
"""

import asyncio
import logging
import re
import uuid
from typing import AsyncGenerator

import httpx

from app import db
from app.config import settings

from supabase import create_client

logger = logging.getLogger(__name__)

# Supabase client — created once, reused for every request.
_supabase_storage = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# ElevenLabs model constants
VOICE_MODEL          = "eleven_flash_v2_5"  # voice command responses — speed first
BRIEFING_VOICE_MODEL = "eleven_v3"          # briefing audio — quality first

_EL_TTS_STREAM_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries, filtering empty strings."""
    parts = _SENTENCE_SPLIT.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _http_tts_sync(text: str, voice_id: str, model: str) -> bytes:
    """
    Call ElevenLabs HTTP streaming TTS endpoint synchronously and return MP3 bytes.
    Called via asyncio.to_thread so it doesn't block the event loop.

    Using the /stream endpoint is lower latency than the WebSocket endpoint when
    the full text is available upfront.
    """
    url = _EL_TTS_STREAM_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model,
    }
    with httpx.Client(timeout=30.0) as client:
        with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            return response.read()


class VoiceService:

    async def stream_tts(
        self,
        text: str,
        voice_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream TTS sentence-by-sentence via ElevenLabs HTTP streaming endpoint.
        Yields raw MP3 bytes for each sentence.

        Uses eleven_flash_v2_5 for lowest latency on voice command responses.

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
                audio_bytes = await asyncio.to_thread(
                    _http_tts_sync, sentence, vid, VOICE_MODEL
                )
                yield audio_bytes
            except Exception:
                logger.exception("ElevenLabs TTS failed for sentence: %.60s…", sentence)
                return  # don't yield silence; let the caller handle

    async def generate_and_store(self, text: str, user_id: str) -> str:
        """
        Generate full TTS audio, upload to Supabase Storage bucket 'felix-audio',
        and return the public URL.

        Uses eleven_v3 for higher quality briefing audio.
        Raises on failure.
        """
        voice_id = await self._get_user_voice_id(user_id)

        # Generate full audio in a thread via HTTP streaming endpoint
        audio_bytes = await asyncio.to_thread(
            _http_tts_sync, text, voice_id, BRIEFING_VOICE_MODEL
        )

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
