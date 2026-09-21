"""Fast Windows SAPI speech output through the installed pyttsx3 bridge."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def select_voice(voices: list[Any], language: str) -> str | None:
    wanted = language.casefold().strip()
    if not wanted:
        return None
    for voice in voices:
        details = " ".join(
            str(value).casefold()
            for value in (
                getattr(voice, "id", ""),
                getattr(voice, "name", ""),
                getattr(voice, "languages", ""),
            )
        )
        if wanted in details or (wanted == "es" and any(token in details for token in ("spanish", "español", "helena", "sabina"))):
            return str(getattr(voice, "id", "")) or None
    return None


def synthesize_to_file(
    text: str,
    output_path: Path,
    language: str = "es",
    rate: int = 175,
    volume: float = 1.0,
    engine_factory: Callable[[], Any] | None = None,
) -> Path:
    if engine_factory is None:
        import pyttsx3

        engine_factory = pyttsx3.init

    output_path.parent.mkdir(parents=True, exist_ok=True)
    engine = engine_factory()
    try:
        voice_id = select_voice(list(engine.getProperty("voices") or []), language)
        if voice_id:
            engine.setProperty("voice", voice_id)
        engine.setProperty("rate", int(rate))
        engine.setProperty("volume", max(0.0, min(1.0, float(volume))))
        engine.save_to_file(text, str(output_path))
        engine.runAndWait()
    finally:
        engine.stop()
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("SAPI no genero el archivo de audio esperado.")
    return output_path
