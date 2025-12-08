# titan/perception/sensors/microphone.py
from __future__ import annotations
import asyncio
import logging
import numpy as np
import sounddevice as sd
import webrtcvad
from typing import Dict, Any, Optional, Callable

from .base import BaseSensor

logger = logging.getLogger(__name__)

# Optional: VOSK offline
try:
    from vosk import Model as VoskModel, KaldiRecognizer  # type: ignore
    _HAS_VOSK = True
except Exception:
    _HAS_VOSK = False

class MicrophoneSensor(BaseSensor):
    name = "microphone"
    version = "1.0.0"

    def __init__(self, *, sample_rate: int = 16000, channels: int = 1, chunk_ms: int = 30, vad_mode: int = 3, stt_backend: str = "vosk", stt_model_path: str = "", loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__(name="microphone", loop=loop)
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.vad = webrtcvad.Vad(vad_mode)
        self.stt_backend = stt_backend
        self.stt_model_path = stt_model_path
        self._stream = None
        self._buffer = bytearray()
        self._on_transcript: Optional[Callable[[str, dict], Any]] = None
        # optional vosk recognizer
        self._vosk_model = None
        self._recognizer = None
        if self.stt_backend == "vosk" and _HAS_VOSK and self.stt_model_path:
            try:
                self._vosk_model = VoskModel(self.stt_model_path)
            except Exception:
                logger.exception("Failed to load Vosk model")

    def set_transcript_callback(self, cb: Callable[[str, dict], Any]):
        """cb(text, meta)"""
        self._on_transcript = cb

    async def start(self):
        """Start capturing audio in a background thread and process chunks in event loop."""
        blocksize = int(self.sample_rate * (self.chunk_ms / 1000.0))
        def callback(indata, frames, time_info, status):
            if status:
                logger.debug("audio status: %s", status)
            # convert to 16-bit PCM
            try:
                data = (indata * 32767).astype(np.int16).tobytes()
            except Exception:
                # fallback to raw bytes if shape unexpected
                data = indata.tobytes()
            asyncio.get_event_loop().call_soon_threadsafe(self._process_audio_chunk, data)

        try:
            self._stream = sd.InputStream(samplerate=self.sample_rate, channels=self.channels, blocksize=blocksize, callback=callback)
            self._stream.start()
            self._running = True
            self._health = {"status": "running"}
            logger.info("MicrophoneSensor started")
        except Exception:
            logger.exception("Failed to start microphone stream")
            self._health = {"status": "error"}

    def _process_audio_chunk(self, data: bytes):
        """
        Called in the event loop thread via call_soon_threadsafe.
        VAD-based buffering and finalization; once speech segment ends we call STT.
        """
        try:
            # webrtcvad expects 16-bit mono frames. We treat every chunk as frame for VAD check.
            try:
                is_speech = self.vad.is_speech(data, sample_rate=self.sample_rate)
            except Exception:
                is_speech = True

            if is_speech:
                self._buffer.extend(data)
            else:
                if len(self._buffer) > 0:
                    audio_segment = bytes(self._buffer)
                    self._buffer = bytearray()
                    asyncio.create_task(self._run_stt(audio_segment))
        except Exception:
            logger.exception("MicrophoneSensor _process_audio_chunk failed")

    async def _run_stt(self, audio_bytes: bytes):
        """
        Run STT according to backend. Returns transcript text or None.
        Emits Option C event: perception.transcript
        """
        text = None
        meta = {"backend": self.stt_backend}
        try:
            if self.stt_backend == "vosk" and _HAS_VOSK and self._vosk_model:
                from vosk import KaldiRecognizer
                rec = KaldiRecognizer(self._vosk_model, self.sample_rate)
                rec.AcceptWaveform(audio_bytes)
                res = rec.Result()
                import json
                parsed = json.loads(res)
                text = parsed.get("text", "")
            elif self.stt_backend in ("openai", "whisper"):
                meta["note"] = f"{self.stt_backend} backend requires external integration"
            else:
                meta["note"] = "no STT backend configured"
        except Exception:
            logger.exception("MicrophoneSensor STT failed")

        if text:
            event = {"sensor": "microphone", "type": "transcript", "text": text, "meta": meta, "ts": asyncio.get_event_loop().time()}
            self._emit(event)
            try:
                if self._on_transcript:
                    res = self._on_transcript(text, meta)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
            except Exception:
                logger.exception("MicrophoneSensor: transcript callback failed")

    async def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("Failed to stop microphone stream")
        self._running = False
        self._health = {"status": "stopped"}
