"""Windows SAPI text-to-speech adapter backed by pyttsx3."""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import AsyncIterator

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderUnavailable


def pyttsx3_available() -> bool:
    """Return whether the optional pyttsx3 bridge can be imported."""
    try:
        import pyttsx3  # noqa: F401

        return True
    except ImportError:
        return False


class Pyttsx3TTS:
    """Synthesize text chunks through the platform's pyttsx3 voice engine."""

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        try:
            import pyttsx3  # noqa: PLC0415

            engine = pyttsx3.init()
        except (ImportError, Exception) as error:
            raise ProviderUnavailable(
                "pyttsx3 is unavailable; install pyttsx3 and enable a system voice", provider="tts"
            ) from error

        loop = asyncio.get_running_loop()
        async for chunk in text:
            context.cancellation.raise_if_cancelled()
            chunk = chunk.strip()
            if not chunk:
                continue

            def _synthesize() -> bytes:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output:
                    output_path = output.name
                try:
                    engine.save_to_file(chunk, output_path)
                    engine.runAndWait()
                    with open(output_path, "rb") as output:
                        return output.read()
                finally:
                    try:
                        os.unlink(output_path)
                    except FileNotFoundError:
                        pass

            yield await loop.run_in_executor(None, _synthesize)
