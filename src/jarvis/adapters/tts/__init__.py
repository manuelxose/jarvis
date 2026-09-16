"""Text-to-speech adapters (local Coqui, Windows SAPI)."""

from .local import LocalTTS, local_tts_available

__all__ = ["LocalTTS", "local_tts_available"]
