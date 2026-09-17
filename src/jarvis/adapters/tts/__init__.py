"""Text-to-speech adapters for local Coqui and Windows SAPI."""

from .local import LocalTTS, local_tts_available
from .pyttsx3 import Pyttsx3TTS, pyttsx3_available
from .resolve import resolve_tts, tts_available, tts_provider

__all__ = [
    "LocalTTS",
    "Pyttsx3TTS",
    "local_tts_available",
    "pyttsx3_available",
    "resolve_tts",
    "tts_available",
    "tts_provider",
]
