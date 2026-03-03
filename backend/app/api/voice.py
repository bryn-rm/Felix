"""
Voice gateway — Phase 3.

WebSocket endpoint /voice/stream

Pipeline:
  browser mic  →  WebSocket (raw audio bytes)
               →  Google Cloud Speech-to-Text V2 (streaming)
               →  Claude Haiku intent parser
               →  voice_router (DB queries → spoken response text)
               →  ElevenLabs TTS (sentence-by-sentence MP3 chunks)
               →  WebSocket back to browser

Auth:
  Browsers cannot set WebSocket headers, so auth is done via the first
  JSON message after connect: {"token": "<supabase_jwt>"}.

Client → Server protocol:
  {"token": "<jwt>"}              — first message, auth handshake
  <binary>                        — raw audio bytes (WebM/Opus from MediaRecorder)
  {"type": "interrupt"}           — cancel current TTS mid-stream
  {"type": "stop_audio"}          — close the STT session / end conversation

Server → Client protocol:
  {"type": "ready"}               — auth OK, start sending audio
  {"type": "transcript", "text": "...", "final": bool}
  {"type": "response_text", "text": "..."}
  <binary>                        — MP3 audio chunk from ElevenLabs
  {"type": "audio_complete"}      — all audio for this response sent
  {"type": "error", "message": "..."}
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import db
from app.config import settings
from app.middleware.auth import get_supabase_client, get_google_credentials
from app.services.ai_service import ai_service
from app.services.gmail_service import GmailService
from app.services.voice_router import route_intent
from app.services.voice_service import voice_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/stream")
async def voice_stream(websocket: WebSocket) -> None:
    await websocket.accept()

    # 1. Authenticate via first JSON message
    user = await _authenticate_ws(websocket)
    if user is None:
        return

    user_id: str = user["id"]

    # 2. Load user settings
    user_settings = await db.query_one(
        "SELECT display_name, felix_voice_id FROM settings WHERE user_id = $1", user_id
    )
    user_name: str = (user_settings or {}).get("display_name") or "there"
    voice_id: str | None = (user_settings or {}).get("felix_voice_id") or None

    # 3. Google credentials — optional; voice works for DB-only intents without them
    gmail: GmailService | None = None
    try:
        creds = await get_google_credentials(user_id)
        gmail = GmailService(creds)
    except Exception:
        logger.info("No Google credentials for voice user %s — Gmail intents disabled", user_id)

    await websocket.send_json({"type": "ready"})

    # Shared state
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
    transcript_queue: asyncio.Queue[str] = asyncio.Queue()
    cancel_tts = asyncio.Event()

    # ------------------------------------------------------------------
    # Task 1 — receive_loop
    # ------------------------------------------------------------------
    async def receive_loop() -> None:
        """Read WebSocket messages and dispatch to queues / events."""
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    await audio_queue.put(message["bytes"])
                elif message.get("text"):
                    try:
                        ctrl = json.loads(message["text"])
                        if ctrl.get("type") == "interrupt":
                            cancel_tts.set()
                        elif ctrl.get("type") == "stop_audio":
                            await audio_queue.put(None)
                    except json.JSONDecodeError:
                        pass
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("receive_loop error for user %s", user_id)
        finally:
            await audio_queue.put(None)  # signal STT to stop

    # ------------------------------------------------------------------
    # Task 2 — stt_loop
    # ------------------------------------------------------------------
    async def stt_loop() -> None:
        """Pipe audio_queue → Google STT; forward transcripts to transcript_queue."""
        try:
            async for text, is_final in _stream_stt(audio_queue):
                try:
                    await websocket.send_json({
                        "type": "transcript",
                        "text": text,
                        "final": is_final,
                    })
                    if is_final and text.strip():
                        await transcript_queue.put(text.strip())
                except Exception:
                    break
        except Exception:
            logger.exception("stt_loop error for user %s", user_id)
        finally:
            await transcript_queue.put("")  # poison pill — signals intent loop to exit

    # ------------------------------------------------------------------
    # Task 3 — intent_tts_loop
    # ------------------------------------------------------------------
    async def intent_tts_loop() -> None:
        """Read final transcripts → intent routing → TTS → stream back."""
        while True:
            transcript = await transcript_queue.get()
            if not transcript:  # poison pill
                break

            cancel_tts.clear()

            # Parse intent with Claude Haiku
            try:
                intent = await ai_service.parse_voice_intent(transcript)
            except Exception:
                logger.exception("parse_voice_intent failed for user %s", user_id)
                intent = {"intent": "general_question", "raw_transcript": transcript}

            # Route to spoken response
            try:
                response_text = await route_intent(intent, user_id, gmail, user_name)
            except Exception:
                logger.exception("route_intent failed for user %s", user_id)
                response_text = "I had trouble handling that. Please try again."

            # Stream TTS back
            try:
                await websocket.send_json({"type": "response_text", "text": response_text})

                async for audio_chunk in voice_service.stream_tts(
                    response_text,
                    voice_id=voice_id,
                    cancel_event=cancel_tts,
                ):
                    if cancel_tts.is_set():
                        break
                    await websocket.send_bytes(audio_chunk)

                await websocket.send_json({"type": "audio_complete"})
            except Exception:
                logger.exception("TTS/send error for user %s", user_id)

            # Persist session log (best-effort)
            try:
                await db.insert("voice_sessions", {
                    "user_id":      user_id,
                    "transcript":   transcript,
                    "intent":       intent,
                    "response":     response_text,
                    "action_taken": intent.get("intent"),
                })
            except Exception:
                logger.warning("Failed to log voice session for user %s", user_id)

    # ------------------------------------------------------------------
    # Run all three tasks; exit cleanly when receive_loop exits
    # ------------------------------------------------------------------
    receive_task = asyncio.create_task(receive_loop())
    stt_task = asyncio.create_task(stt_loop())
    intent_task = asyncio.create_task(intent_tts_loop())

    try:
        await receive_task  # blocks until disconnect or stop_audio
    except Exception:
        logger.exception("Voice session error for user %s", user_id)
    finally:
        stt_task.cancel()
        intent_task.cancel()
        await asyncio.gather(stt_task, intent_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Google STT V2 streaming helper
# ---------------------------------------------------------------------------

async def _stream_stt(
    audio_queue: asyncio.Queue,
) -> AsyncGenerator[tuple[str, bool], None]:
    """
    Pipe raw audio bytes from audio_queue through Google Cloud Speech-to-Text V2.
    Yields (transcript_text, is_final) tuples.

    Stops when audio_queue yields None (sentinel).
    """
    from google.cloud.speech_v2 import SpeechAsyncClient
    from google.cloud.speech_v2.types import cloud_speech

    project_id = settings.GCP_PROJECT_ID

    recognizer = f"projects/{project_id}/locations/global/recognizers/_"
    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDecodingConfig(),
        language_codes=["en-US"],
        model="long",
    )
    streaming_config = cloud_speech.StreamingRecognitionConfig(
        config=config,
        streaming_features=cloud_speech.StreamingRecognitionFeatures(
            interim_results=True,
        ),
    )

    async def request_generator():
        # First request carries the recognizer path + streaming config
        yield cloud_speech.StreamingRecognizeRequest(
            recognizer=recognizer,
            streaming_config=streaming_config,
        )
        # Subsequent requests carry audio chunks
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                return  # sentinel — stop the generator
            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    try:
        async with SpeechAsyncClient() as stt_client:
            async for response in stt_client.streaming_recognize(request_generator()):
                for result in response.results:
                    if result.alternatives:
                        yield result.alternatives[0].transcript, result.is_final
    except Exception:
        logger.exception("Google STT V2 streaming error")


# ---------------------------------------------------------------------------
# WebSocket auth helper
# ---------------------------------------------------------------------------

async def _authenticate_ws(websocket: WebSocket) -> dict | None:
    """
    Validate the Supabase JWT received in the first WebSocket message.
    Expects: {"token": "<supabase_jwt>"}

    Returns user dict on success, or None (after sending error + closing) on failure.
    """
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        msg = json.loads(raw)
        token: str = msg.get("token", "").strip()
    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "message": "Auth timeout"})
        await websocket.close(code=4001)
        return None
    except Exception:
        await websocket.send_json({"type": "error", "message": "Invalid auth message"})
        await websocket.close(code=4001)
        return None

    if not token:
        await websocket.send_json({"type": "error", "message": "Missing token"})
        await websocket.close(code=4001)
        return None

    try:
        # get_user() is synchronous HTTP — run in a thread to avoid blocking the event loop.
        result = await asyncio.to_thread(get_supabase_client().auth.get_user, token)
        if not result or not result.user:
            raise ValueError("No user returned")
        return {"id": result.user.id, "email": result.user.email}
    except Exception:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=4001)
        return None
