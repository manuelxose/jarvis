"""Windows SAPI shared-recognizer speech-to-text adapter."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Any

from jarvis.core.contracts import Transcript, TurnContext
from jarvis.core.errors import ProviderUnavailable


def sapi_stt_available() -> bool:
    """Return whether the optional COM bridge can be imported."""
    try:
        import comtypes.client  # noqa: F401

        return True
    except ImportError:
        return False


class _RecognitionSink:
    """Collect final dictation results emitted by SAPI's shared recognizer."""

    def __init__(self) -> None:
        self.transcripts: list[str] = []

    def OnRecognition(
        self,
        _stream_number: int,
        _stream_position: int,
        _recognition_type: int,
        result: Any,
    ) -> None:
        try:
            text = result.GetText(0, -1, True).strip()
        except Exception:
            return
        if text:
            self.transcripts.append(text)


class SapiSTT:
    """Receive Windows shared-recognizer dictation while audio capture is active."""

    async def transcribe(self, audio: AsyncIterator[bytes], context: TurnContext) -> AsyncIterator[Transcript]:
        try:
            from comtypes import client  # noqa: PLC0415

            recognizer = client.CreateObject("SAPI.SpSharedRecognizer")
            reco_context = recognizer.CreateRecoContext()
            grammar = reco_context.CreateGrammar()
            grammar.DictationLoad()
            grammar.DictationSetState(1)
            sink = _RecognitionSink()
            connection = client.GetEvents(reco_context, sink)
        except (ImportError, Exception) as error:
            raise ProviderUnavailable(
                "Windows SAPI dictation is unavailable; install comtypes and enable SAPI", provider="stt"
            ) from error

        try:
            async for _chunk in audio:
                context.cancellation.raise_if_cancelled()
                # Let COM callbacks enqueue results while the shared microphone is active.
                await asyncio.sleep(0)
        finally:
            try:
                grammar.DictationSetState(0)
                connection.disconnect()
            except Exception:
                pass

        for text in sink.transcripts:
            yield Transcript(text, is_final=True)
