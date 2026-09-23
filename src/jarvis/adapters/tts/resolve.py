"""Configured text-to-speech provider resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.core.contracts import TextToSpeech
from jarvis.core.errors import ProviderConfigError

from .alibaba_qwen import AlibabaQwenTTS, alibaba_qwen_tts_available
from .local import LocalTTS, local_tts_available
from .pyttsx3 import Pyttsx3TTS, pyttsx3_available
from .qwen_clone import QwenCloneTTS, default_worker_python, worker_command

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig

_PROVIDERS = {"local", "sapi", "alibaba_qwen", "qwen_clone"}


def tts_provider(config: RuntimeConfig) -> str:
    """Return the normalized configured TTS provider or raise a typed error."""
    provider = config.tts.provider.strip().lower()
    if provider in _PROVIDERS:
        return provider
    if provider == "cloud":
        raise ProviderConfigError("cloud TTS is not supported in the offline loop", provider="tts")
    raise ProviderConfigError("unknown TTS provider: {!r}".format(config.tts.provider), provider="tts")


def resolve_tts(config: RuntimeConfig) -> TextToSpeech:
    """Build the configured TTS adapter without importing optional packages."""
    provider = tts_provider(config)
    if provider == "sapi":
        return Pyttsx3TTS()
    if provider == "alibaba_qwen":
        return AlibabaQwenTTS.from_config(config)
    if provider == "qwen_clone":
        return build_qwen_clone(config)
    return LocalTTS(language=config.tts.language)


def build_qwen_clone(config: RuntimeConfig) -> QwenCloneTTS:
    from jarvis.voice_profile import default_profile_dir  # noqa: PLC0415

    command = worker_command(
        config.tts.worker_python or default_worker_python(),
        profile_dir=config.tts.profile_dir or str(default_profile_dir()),
        language=config.tts.language,
        chunk_size=config.tts.chunk_size,
        model=config.tts.model,
    )
    return QwenCloneTTS(command, stderr_path=str(Path("logs") / "tts-worker.log"))


def tts_available(config: RuntimeConfig) -> bool:
    """Return availability for the configured TTS provider only."""
    provider = tts_provider(config)
    if provider == "sapi":
        return pyttsx3_available()
    if provider == "alibaba_qwen":
        return alibaba_qwen_tts_available(config)
    if provider == "qwen_clone":
        return Path(config.tts.worker_python or default_worker_python()).is_file()
    return local_tts_available()
