"""
Voice gateway — Phase 3.

WebSocket endpoint that pipes browser audio → Google STT → Claude intent
parser → action handler → ElevenLabs TTS → browser audio.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.middleware.auth import get_current_user

router = APIRouter()


@router.websocket("/stream")
async def voice_stream(websocket: WebSocket):
    """
    TODO Phase 3: full WebSocket pipeline.

    Protocol (client → server):
      - First message: JSON {"token": "<supabase_jwt>"}  (auth handshake)
      - Subsequent messages: raw audio bytes (PCM 16kHz mono)

    Protocol (server → client):
      - {"type": "transcript", "text": "...", "final": bool}
      - {"type": "response_text", "text": "..."}
      - raw bytes (ElevenLabs audio stream)
      - {"type": "audio_complete"}
    """
    await websocket.accept()
    await websocket.send_json({"type": "error", "message": "Voice not yet implemented (Phase 3)"})
    await websocket.close()
