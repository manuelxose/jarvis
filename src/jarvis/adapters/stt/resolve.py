"""Configured speech-to-text provider resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.core.contracts import SpeechToText
from jarvis.core.errors import ProviderConfigError

from .sapi import SapiSTT, sapi_stt_available
from .whisper import WhisperSTT, whisper_available

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig


def stt_provider(config: RuntimeConfig) -> str:
    """Return the normalized configured STT provider or raise a typed error."""
    provider = config.stt.provider.strip().lower()
    if provider in {"whisper", "sapi"}:
        return provider
    if provider == "cloud":
        raise ProviderConfigError("cloud STT is not supported in the offline loop", provider="stt")
    raise ProviderConfigError("unknown STT provider: {!r}".format(config.stt.provider), provider="stt")


def resolve_stt(config: RuntimeConfig) -> SpeechToText:
    """Build the configured local STT adapter without importing optional packages."""
    provider = stt_provider(config)
    if provider == "sapi":
        return SapiSTT()
    return WhisperSTT(
        model=config.stt.model,
        language=config.stt.language,
        device=config.stt.device,
        sample_rate=config.audio.sample_rate,
    )


def stt_available(config: RuntimeConfig) -> bool:
    """Return availability for the configured STT provider only."""
    if stt_provider(config) == "sapi":
        return sapi_stt_available()
    return whisper_available()
