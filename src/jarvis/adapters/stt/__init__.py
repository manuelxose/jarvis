"""Speech-to-text adapters for local whisper and Windows SAPI."""

from .resolve import resolve_stt, stt_available, stt_provider
from .sapi import SapiSTT, sapi_stt_available
from .whisper import WhisperSTT, whisper_available

__all__ = [
    "SapiSTT",
    "WhisperSTT",
    "resolve_stt",
    "sapi_stt_available",
    "stt_available",
    "stt_provider",
    "whisper_available",
]
