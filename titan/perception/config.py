# titan/perception/config.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class PerceptionConfig:
    # sensors toggles
    enable_keyboard: bool = True
    enable_mouse: bool = True
    enable_window_monitor: bool = True
    enable_notifications: bool = True
    enable_microphone: bool = True
    enable_wakeword: bool = False  # optional & licensed

    # audio / stt settings
    stt_backend: str = "vosk"  # "vosk" | "openai" | "whisper" | "none"
    stt_model_path: str = ""   # for vosk/local models
    vad_mode: int = 3          # 0-3 aggressiveness for webrtcvad

    # runtime tuning
    threadpool_workers: int = 8
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 30
