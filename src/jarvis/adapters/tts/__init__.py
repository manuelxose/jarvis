"""Text-to-speech adapters for local Coqui, Windows SAPI, and Alibaba Qwen cloud."""

from .ack_cache import AckAudioCache, bytes_to_stream, cache_key
from .alibaba_qwen import AlibabaQwenTTS, alibaba_qwen_tts_available
from .fallback import TTSChain
from .local import LocalTTS, local_tts_available
from .pyttsx3 import Pyttsx3TTS, pyttsx3_available
from .resolve import resolve_tts, tts_available, tts_provider

__all__ = [
    "AckAudioCache",
    "AlibabaQwenTTS",
    "LocalTTS",
    "Pyttsx3TTS",
    "TTSChain",
    "alibaba_qwen_tts_available",
    "bytes_to_stream",
    "cache_key",
    "local_tts_available",
    "pyttsx3_available",
    "resolve_tts",
    "tts_available",
    "tts_provider",
]
