"""Configured text-to-speech provider resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.core.contracts import TextToSpeech
from jarvis.core.errors import ProviderConfigError

from .local import LocalTTS, local_tts_available
from .pyttsx3 import Pyttsx3TTS, pyttsx3_available

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig


def tts_provider(config: RuntimeConfig) -> str:
    """Return the normalized configured TTS provider or raise a typed error."""
    provider = config.tts.provider.strip().lower()
    if provider in {"local", "sapi"}:
        return provider
    if provider == "cloud":
        raise ProviderConfigError("cloud TTS is not supported in the offline loop", provider="tts")
    raise ProviderConfigError("unknown TTS provider: {!r}".format(config.tts.provider), provider="tts")


def resolve_tts(config: RuntimeConfig) -> TextToSpeech:
    """Build the configured local TTS adapter without importing optional packages."""
    if tts_provider(config) == "sapi":
        return Pyttsx3TTS()
    return LocalTTS(language=config.tts.language)


def tts_available(config: RuntimeConfig) -> bool:
    """Return availability for the configured TTS provider only."""
    if tts_provider(config) == "sapi":
        return pyttsx3_available()
    return local_tts_available()
