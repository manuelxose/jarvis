"""Text-to-speech adapters for local Coqui, Windows SAPI, and ElevenLabs cloud."""

from .elevenlabs import ElevenLabsTTS, elevenlabs_available
from .local import LocalTTS, local_tts_available
from .pyttsx3 import Pyttsx3TTS, pyttsx3_available
from .resolve import resolve_tts, tts_available, tts_provider

__all__ = [
    "ElevenLabsTTS",
    "LocalTTS",
    "Pyttsx3TTS",
    "elevenlabs_available",
    "local_tts_available",
    "pyttsx3_available",
    "resolve_tts",
    "tts_available",
    "tts_provider",
]
