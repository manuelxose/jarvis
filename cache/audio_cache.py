from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Iterable


LOGGER = logging.getLogger(__name__)

COMMON_RESPONSES = [
    "Entendido",
    "En un momento",
    "En que mas puedo ayudarte?",
    "Procesando tu solicitud",
    "Jarvis a tu servicio",
    "No he podido completar esa accion",
    "Busqueda completada",
    "Comando ejecutado",
]


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _text_hash(text: str) -> str:
    return hashlib.sha1(_normalize_text(text).encode("utf-8")).hexdigest()


def _text_slug(text: str) -> str:
    normalized = _normalize_text(text)
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug or "audio"


class AudioCache:
    """Cache manager for generated TTS WAV files."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cached_audio(self, text: str) -> Path | None:
        hashed_path = self.cache_dir / f"{_text_hash(text)}.wav"
        if hashed_path.exists():
            return hashed_path

        named_path = self.cache_dir / f"{_text_slug(text)}.wav"
        if named_path.exists():
            return named_path

        return None

    def cache_audio(self, text: str, audio_bytes: bytes, prefer_named: bool = False) -> Path:
        filename = f"{_text_slug(text)}.wav" if prefer_named else f"{_text_hash(text)}.wav"
        output_path = self.cache_dir / filename
        output_path.write_bytes(audio_bytes)
        return output_path


def pregenerate_common_responses(
    synthesizer: Callable[[str, Path], None],
    cache: AudioCache,
    phrases: Iterable[str] | None = None,
    background: bool = True,
    is_busy: Callable[[], bool] | None = None,
) -> threading.Thread | None:
    """
    Pre-generate common responses.

    `synthesizer` receives (text, output_path).

    When `is_busy` is provided, each phrase is gated behind it: the worker
    pauses while it returns True so background TTS work never contends with
    active speech-to-text capture.
    """
    common_phrases = list(phrases) if phrases is not None else COMMON_RESPONSES

    def _worker() -> None:
        for phrase in common_phrases:
            if is_busy is not None:
                while is_busy():
                    time.sleep(0.05)
            cached = cache.get_cached_audio(phrase)
            if cached:
                continue
            named_path = cache.cache_dir / f"{_text_slug(phrase)}.wav"
            try:
                synthesizer(phrase, named_path)
                LOGGER.info("Pre-generated cache audio: %s", named_path.name)
            except Exception as exc:
                LOGGER.warning("Failed to pre-generate '%s': %s", phrase, exc)

    if background:
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    _worker()
    return None

