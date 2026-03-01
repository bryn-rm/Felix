"""
Voice pipeline — Phase 3.

STT: Google Cloud Speech-to-Text V2 (streaming)
TTS: ElevenLabs Turbo v2.5 (streaming)
"""

from app.config import settings

FELIX_VOICE_CONFIG = {
    "voice_id": settings.FELIX_VOICE_ID,
    "model_id": "eleven_turbo_v2_5",
    "voice_settings": {
        "stability": 0.60,
        "similarity_boost": 0.80,
        "style": 0.15,
        "use_speaker_boost": True,
    },
}


class VoiceService:

    async def stream_tts(self, text: str):
        """
        TODO Phase 3: stream audio chunks from ElevenLabs.
        Yields bytes.
        """
        raise NotImplementedError

    async def generate_and_store(self, text: str, user_id: str) -> str:
        """
        TODO Phase 4: generate full TTS audio, upload to Supabase Storage,
        return public URL (used for morning briefings).
        """
        raise NotImplementedError


voice_service = VoiceService()
