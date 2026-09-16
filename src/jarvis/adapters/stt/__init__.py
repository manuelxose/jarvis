"""Speech-to-text adapters (local faster-whisper, cloud)."""

from .whisper import WhisperSTT, whisper_available

__all__ = ["WhisperSTT", "whisper_available"]
