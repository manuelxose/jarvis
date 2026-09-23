"""Speech-to-text adapters for local whisper, Windows SAPI, and Alibaba Qwen cloud."""

from .alibaba_qwen import AlibabaQwenSTT, alibaba_qwen_stt_available
from .resolve import resolve_stt, stt_available, stt_provider
from .sapi import SapiSTT, sapi_stt_available
from .whisper import WhisperSTT, whisper_available

__all__ = [
    "AlibabaQwenSTT",
    "SapiSTT",
    "WhisperSTT",
    "alibaba_qwen_stt_available",
    "resolve_stt",
    "sapi_stt_available",
    "stt_available",
    "stt_provider",
    "whisper_available",
]
