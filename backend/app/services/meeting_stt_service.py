"""
Meeting capture STT relay — Phase 2 of the meeting-capture feature.

Two independent Google Speech-to-Text **V2** streaming sessions per meeting, one
per channel ("me" = the user's mic, "them" = the shared tab audio). Each channel
carries a fixed speaker tag, so we get speaker attribution for free without
diarization.

This is deliberately NOT a reuse of ``voice._stream_stt``: that path uses
``AutoDetectDecodingConfig`` and a single stream with no rollover. Meeting capture
sends explicit LINEAR16 / 16 kHz PCM and meetings run far longer than Google's
~5-minute single-stream cap, so each channel must roll its stream over without
losing or mis-timing finals.

Highest-risk piece of the feature (built and unit-tested in isolation first). The
Google call is isolated in ``MeetingSTTChannel._recognize_stream`` so the rollover
+ offset math can be exercised with a fake stream and no network.

Wire contract:
  - Only **finals** are persisted to ``meeting_transcript_segments``. Interims are
    pushed to the WS for live display and discarded.
  - ``ts_start``/``ts_end`` are **meeting-relative seconds**, computed from bytes of
    audio consumed (16 kHz · 2 bytes/sample), NOT wall-clock — deterministic and
    rollover-safe.
  - A final is persisted **only while the meeting is still ``status='recording'``**
    (an atomic conditional insert — see ``_persist_final``). Once a meeting leaves
    'recording' by ANY path (auto-end sweep, budget-429, manual/admin stop, …),
    zero further segments land for it — audio in flight can't orphan itself into
    an already-summarized meeting.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

from app import db
from app.config import settings
from app.utils.background import spawn

logger = logging.getLogger(__name__)

# LINEAR16, 16 kHz, mono → 2 bytes per sample.
SAMPLE_RATE_HZ = 16000
BYTES_PER_SECOND = SAMPLE_RATE_HZ * 2

# Map the wire channel byte (first byte of each binary audio frame) → speaker tag.
CHANNEL_BYTE_TO_SPEAKER = {0x00: "me", 0x01: "them"}

# Guard against a tight reconnect loop if a stream keeps failing with no progress.
_MAX_ZERO_PROGRESS_FAILURES = 3

SendJson = Callable[[dict], Awaitable[None]]


@dataclass
class STTResult:
    """One recognition result, with offsets **relative to the current stream**."""
    transcript: str
    is_final: bool
    start_s: float
    end_s: float


def _duration_to_seconds(value) -> float:
    """Coerce a Google Duration / timedelta / number to float seconds."""
    if value is None:
        return 0.0
    if hasattr(value, "total_seconds"):           # datetime.timedelta (proto-plus)
        return float(value.total_seconds())
    if hasattr(value, "seconds"):                 # raw Duration proto
        return float(value.seconds) + float(getattr(value, "nanos", 0)) / 1e9
    return float(value)


class MeetingSTTChannel:
    """One speaker channel: an audio queue, a rolling STT stream, final persistence."""

    def __init__(
        self,
        *,
        meeting_id: str,
        user_id: str,
        speaker: str,
        send_json: SendJson,
        language: str = "en-US",
        base_offset_s: float = 0.0,
    ) -> None:
        self.meeting_id = meeting_id
        self.user_id = user_id
        self.speaker = speaker
        self.send_json = send_json
        self.language = language
        # Meeting-relative seconds already covered by persisted segments. Seeded
        # by the WS layer from MAX(ts_end) so a reconnected session continues the
        # meeting clock instead of restarting at 0 (which would reorder the
        # transcript when segments are read back ORDER BY ts_start).
        self.base_offset_s = base_offset_s

        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._bytes_fed = 0          # total audio bytes pulled from the queue (the clock)
        self._closed = False
        # Latched once the meeting is observed to have left 'recording' (the
        # guarded insert persisted nothing): stop persisting/rolling over.
        self._finalized = False

    # -- producer side (called by the WS layer) ------------------------------

    async def push_audio(self, chunk: bytes) -> None:
        if not self._closed:
            await self._queue.put(chunk)

    async def close(self) -> None:
        """Signal end-of-audio. The run loop drains, finalizes, then exits."""
        self._closed = True
        await self._queue.put(None)

    # -- audio draining ------------------------------------------------------

    async def _drain_audio(self) -> AsyncIterator[bytes]:
        """
        Yield audio chunks for **one** stream, counting bytes as the clock.

        Returns (stream ends) on the ``None`` close sentinel. Across a rollover a
        fresh generator is created but ``self._bytes_fed`` persists, so the next
        stream's offset continues meeting-relative.
        """
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            self._bytes_fed += len(chunk)
            yield chunk

    # -- the Google call (isolated for testability) --------------------------

    async def _recognize_stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[STTResult]:
        """
        Stream ``audio`` through Google Speech V2 and yield STTResults with
        stream-relative offsets. Overridden in tests with a fake (no network).
        Raises / returns when the stream ends or Google caps it (~5 min) — the
        caller treats that as a rollover signal.
        """
        from google.cloud.speech_v2 import SpeechAsyncClient
        from google.cloud.speech_v2.types import cloud_speech

        recognizer = f"projects/{settings.GCP_PROJECT_ID}/locations/global/recognizers/_"
        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE_HZ,
                audio_channel_count=1,
            ),
            language_codes=[self.language],
            model="long",
            features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
        )
        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(interim_results=True),
        )

        async def request_generator():
            yield cloud_speech.StreamingRecognizeRequest(
                recognizer=recognizer, streaming_config=streaming_config
            )
            async for chunk in audio:
                yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

        async with SpeechAsyncClient() as client:
            stream = await client.streaming_recognize(requests=request_generator())
            async for response in stream:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    alt = result.alternatives[0]
                    end_s = _duration_to_seconds(getattr(result, "result_end_offset", None))
                    start_s = end_s
                    words = getattr(alt, "words", None)
                    if words:
                        start_s = _duration_to_seconds(getattr(words[0], "start_offset", None))
                    yield STTResult(
                        transcript=alt.transcript,
                        is_final=bool(result.is_final),
                        start_s=start_s,
                        end_s=end_s,
                    )

    # -- persistence + live emit ---------------------------------------------

    async def _persist_final(self, text: str, ts_start: float, ts_end: float) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._finalized:
            # Meeting already observed as no-longer-recording — don't re-query.
            return
        # Atomic guard: persist ONLY while the meeting is still 'recording'. A
        # single conditional insert (no separate status read) closes the TOCTOU
        # window, so a status flip from ANY path — the auto-end sweep, a
        # budget-429 on /end, a manual/admin stop, a future one — can't orphan a
        # segment into an already-finalized meeting. Invariant: once a meeting
        # leaves 'recording', zero further segments are persisted for it.
        row = await db.query_one(
            """
            INSERT INTO meeting_transcript_segments
                (user_id, meeting_id, speaker, text, ts_start, ts_end)
            SELECT $1::uuid, $2::uuid, $3, $4, $5, $6
            WHERE EXISTS (
                SELECT 1 FROM meetings
                WHERE id = $2::uuid AND user_id = $1::uuid AND status = 'recording'
            )
            RETURNING id
            """,
            self.user_id, self.meeting_id, self.speaker, text, ts_start, ts_end,
        )
        if row is None:
            # The meeting is no longer recording. Latch it so the run loop stops
            # rolling into new streams and later finals short-circuit above, and
            # skip the live emit (nothing was stored).
            self._finalized = True
            return
        await self._emit(text, ts_start, is_final=True)

    async def _emit(self, text: str, ts_start: float, *, is_final: bool) -> None:
        try:
            await self.send_json(
                {
                    "type": "transcript",
                    "speaker": self.speaker,
                    "text": text,
                    "is_final": is_final,
                    "ts_start": ts_start,
                }
            )
        except Exception:
            # WS may have dropped; finals are already persisted, so just log.
            logger.debug("meeting STT emit failed (channel=%s)", self.speaker, exc_info=True)

    async def _notify_channel_stopped(self) -> None:
        """Tell the client this channel stopped transcribing (non-fatal)."""
        try:
            await self.send_json({"type": "status", "status": "channel_stopped", "speaker": self.speaker})
        except Exception:
            logger.debug("meeting STT stop-notify failed (channel=%s)", self.speaker, exc_info=True)

    # -- the run loop (rollover lives here) ----------------------------------

    async def run(self) -> None:
        """Run streams back-to-back until closed, rolling over on stream end/timeout."""
        zero_progress_failures = 0
        while not self._closed and not self._finalized:
            stream_start_offset = self.base_offset_s + self._bytes_fed / BYTES_PER_SECOND
            bytes_at_start = self._bytes_fed
            try:
                async for r in self._recognize_stream(self._drain_audio()):
                    ts_start = stream_start_offset + r.start_s
                    ts_end = stream_start_offset + r.end_s
                    try:
                        if r.is_final:
                            await self._persist_final(r.transcript, ts_start, ts_end)
                        else:
                            await self._emit(r.transcript, ts_start, is_final=False)
                    except Exception:
                        # A persist/emit failure must NOT propagate to the rollover
                        # handler below — that would misclassify a DB error as a
                        # stream-end and needlessly tear down a healthy stream. Log
                        # and keep consuming this stream.
                        logger.warning(
                            "meeting STT persist/emit failed (channel=%s, meeting=%s)",
                            self.speaker, self.meeting_id, exc_info=True,
                        )
            except Exception:
                # Google caps a single stream (~5 min, OUT_OF_RANGE) or a transient
                # error. If still recording, roll over into a fresh stream.
                logger.info(
                    "meeting STT stream ended (channel=%s, meeting=%s); rolling over",
                    self.speaker, self.meeting_id, exc_info=True,
                )

            # Stop on client close, or once the meeting has left 'recording' (the
            # guarded insert latched _finalized) — don't roll into a new stream
            # only to persist nothing.
            if self._closed or self._finalized:
                break

            if self._bytes_fed == bytes_at_start:
                # Stream ended without consuming any audio → likely a hard failure,
                # not a real rollover. Avoid a tight reconnect spin.
                zero_progress_failures += 1
                if zero_progress_failures >= _MAX_ZERO_PROGRESS_FAILURES:
                    logger.warning(
                        "meeting STT channel %s gave up after %d zero-progress failures",
                        self.speaker, zero_progress_failures,
                    )
                    # Surface the dead channel so the client isn't left believing
                    # this speaker is still being transcribed (the other channel
                    # may still be healthy, so don't tear the whole meeting down).
                    await self._notify_channel_stopped()
                    break
                await asyncio.sleep(0.5)
            else:
                zero_progress_failures = 0


class MeetingSTTSession:
    """Owns the two channels for a meeting and fans audio frames to them."""

    def __init__(
        self,
        *,
        meeting_id: str,
        user_id: str,
        send_json: SendJson,
        language: str = "en-US",
        base_offset_s: float = 0.0,
    ) -> None:
        self.meeting_id = meeting_id
        self.user_id = user_id
        self.channels: dict[str, MeetingSTTChannel] = {
            speaker: MeetingSTTChannel(
                meeting_id=meeting_id,
                user_id=user_id,
                speaker=speaker,
                send_json=send_json,
                language=language,
                base_offset_s=base_offset_s,
            )
            for speaker in ("me", "them")
        }
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        for speaker, channel in self.channels.items():
            self._tasks.append(
                spawn(channel.run(), name=f"meeting_stt_{self.meeting_id}_{speaker}")
            )

    async def feed(self, channel_byte: int, pcm: bytes) -> None:
        """Route one binary audio frame (demuxed channel byte) to its channel."""
        speaker = CHANNEL_BYTE_TO_SPEAKER.get(channel_byte)
        if speaker is None:
            logger.debug("meeting STT: unknown channel byte %r", channel_byte)
            return
        await self.channels[speaker].push_audio(pcm)

    async def stop(self) -> None:
        """Close both channels, drain their final results, and await the run tasks."""
        for channel in self.channels.values():
            await channel.close()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()


def session(
    meeting_id: str,
    user_id: str,
    send_json: SendJson,
    *,
    language: str = "en-US",
    start_offset_s: float = 0.0,
) -> MeetingSTTSession:
    """Factory matching the §2.2 call shape; the WS layer drives start/feed/stop.

    ``start_offset_s`` seeds the meeting clock so a reconnected session keeps
    timestamps monotonic with the segments already persisted (see Phase 6 /
    the reconnect-ordering fix).
    """
    return MeetingSTTSession(
        meeting_id=meeting_id,
        user_id=user_id,
        send_json=send_json,
        language=language,
        base_offset_s=start_offset_s,
    )
