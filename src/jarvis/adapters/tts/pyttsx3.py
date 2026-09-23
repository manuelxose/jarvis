"""Windows SAPI text-to-speech fallback (direct SAPI through comtypes).

pyttsx3's sapi5 driver hangs on the second ``runAndWait()`` of an engine
(measured on the target laptop), which froze a turn after its first
sentence. SAPI is driven directly instead: a synchronous ``SpVoice.Speak``
into an ``SpFileStream``, on ONE dedicated thread (SAPI objects are COM
apartment objects). The system default voice is used (Spanish "Helena" on
the target machine); touching ``SpVoice.Voice`` via comtypes also hung there.
A watchdog abandons a stuck SAPI thread so one hang cannot freeze Jarvis.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
import sys
import tempfile
from typing import Any, AsyncIterator

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderUnavailable

_SSFM_CREATE_FOR_WRITE = 3
_SVSF_DEFAULT = 0  # synchronous Speak


def pyttsx3_available() -> bool:
    """Return whether Windows SAPI can be driven (comtypes importable)."""
    if "comtypes.client" in sys.modules:
        return sys.modules["comtypes.client"] is not None
    try:
        return importlib.util.find_spec("comtypes") is not None
    except (ImportError, ValueError):
        return False


class Pyttsx3TTS:
    """Synthesize text chunks with the Windows SAPI default voice."""

    name = "sapi"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sapi")
        self._voice: Any = None

    def _voice_on_thread(self, client: Any) -> Any:
        if self._voice is None:
            try:
                import comtypes  # noqa: PLC0415

                comtypes.CoInitialize()
            except (ImportError, OSError, AttributeError):
                pass
            self._voice = client.CreateObject("SAPI.SpVoice")
        return self._voice

    def _synthesize_blocking(self, chunk: str) -> bytes:
        try:
            import comtypes.client as client  # noqa: PLC0415

            voice = self._voice_on_thread(client)
        except Exception as error:
            raise ProviderUnavailable("Windows SAPI is unavailable", provider="tts") from error
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output:
            output_path = output.name
        try:
            stream = client.CreateObject("SAPI.SpFileStream")
            stream.Open(output_path, _SSFM_CREATE_FOR_WRITE)
            try:
                voice.AudioOutputStream = stream
                voice.Speak(chunk, _SVSF_DEFAULT)
            finally:
                stream.Close()
            with open(output_path, "rb") as output:
                return output.read()
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        if not pyttsx3_available():
            raise ProviderUnavailable("Windows SAPI is unavailable (comtypes missing)", provider="tts")
        loop = asyncio.get_running_loop()
        async for chunk in text:
            context.cancellation.raise_if_cancelled()
            chunk = chunk.strip()
            if not chunk:
                continue
            future = loop.run_in_executor(self._executor, self._synthesize_blocking, chunk)
            try:
                yield await asyncio.wait_for(future, self._timeout)
            except asyncio.TimeoutError:
                # Abandon the stuck thread (and its apartment-bound voice).
                self._executor.shutdown(wait=False)
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sapi")
                self._voice = None
                raise ProviderUnavailable("Windows SAPI synthesis timed out", provider="tts") from None
