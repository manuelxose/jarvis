"""Configured text-to-speech provider resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.core.contracts import TextToSpeech
from jarvis.core.errors import ProviderConfigError

from .elevenlabs import ElevenLabsTTS, elevenlabs_available
from .local import LocalTTS, local_tts_available
from .pyttsx3 import Pyttsx3TTS, pyttsx3_available

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig


def tts_provider(config: RuntimeConfig) -> str:
    """Return the normalized configured TTS provider or raise a typed error."""
    provider = config.tts.provider.strip().lower()
    if provider in {"local", "sapi", "elevenlabs"}:
        return provider
    if provider == "cloud":
        raise ProviderConfigError("cloud TTS is not supported in the offline loop", provider="tts")
    raise ProviderConfigError("unknown TTS provider: {!r}".format(config.tts.provider), provider="tts")


def resolve_tts(config: RuntimeConfig) -> TextToSpeech:
    """Build the configured TTS adapter without importing optional packages."""
    provider = tts_provider(config)
    if provider == "sapi":
        return Pyttsx3TTS()
    if provider == "elevenlabs":
        return ElevenLabsTTS(api_key=config.tts.api_key, voice_id=config.tts.voice, language=config.tts.language)
    return LocalTTS(language=config.tts.language)


def tts_available(config: RuntimeConfig) -> bool:
    """Return availability for the configured TTS provider only."""
    provider = tts_provider(config)
    if provider == "sapi":
        return pyttsx3_available()
    if provider == "elevenlabs":
        return elevenlabs_available(api_key=config.tts.api_key, voice_id=config.tts.voice)
    return local_tts_available()
