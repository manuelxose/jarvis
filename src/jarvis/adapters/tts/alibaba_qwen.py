"""Cloud Alibaba Model Studio (Qwen) realtime streaming TTS adapter.

Wraps the official ``dashscope`` SDK's ``audio.qwen_tts_realtime.QwenTtsRealtime``
(a threading/callback WebSocket API, not asyncio) behind the ``TextToSpeech``
contract. See ``jarvis.adapters._dashscope`` for region/auth wiring and
docs/engineering/alibaba-qwen-voice-research.md for the sourcing behind the
model id, event shape, and error-mapping choices below.

Audio arrives as ``response.audio.delta`` events with base64-encoded PCM in
their ``delta`` field (confirmed by reading the SDK's ``on_message`` dispatch,
not a documented example) -- this was read directly from the installed
``dashscope`` package source, not guessed from prose docs.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled

from .._dashscope import configure_dashscope, dashscope_available

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig

_CONNECT_TIMEOUT_SECONDS = 10.0


def alibaba_qwen_tts_available(config: "RuntimeConfig") -> bool:
    """Return whether enough configuration is present to call Alibaba (no network probe)."""
    if not dashscope_available():
        return False
    if not config.tts.api_key:
        return False
    if not config.tts.voice:
        return False
    if config.alibaba.region == "singapore" and not config.alibaba.workspace_id:
        return False
    return True


class AlibabaQwenTTS:
    """Synthesize text chunks through Alibaba's realtime TTS WebSocket."""

    name = "alibaba_qwen"

    def __init__(
        self,
        *,
        api_key: Optional[str],
        model: str,
        voice_id: str,
        language: str,
        config: "RuntimeConfig",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id
        self.language = language
        self._config = config

    @classmethod
    def from_config(cls, config: "RuntimeConfig") -> "AlibabaQwenTTS":
        return cls(
            api_key=config.tts.api_key,
            model=config.alibaba.tts_model,
            voice_id=config.tts.voice,
            language=config.tts.language,
            config=config,
        )

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        if not dashscope_available():
            raise ProviderUnavailable("dashscope is not installed; alibaba_qwen TTS unavailable", provider=self.name)
        if not self.voice_id:
            raise ProviderConfigError("alibaba_qwen has no voice configured", provider=self.name)
        session = configure_dashscope(self._config, api_key=self.api_key, provider=self.name)

        from dashscope.audio.qwen_tts_realtime import (  # noqa: PLC0415
            QwenTtsRealtime,
            QwenTtsRealtimeCallback,
        )

        loop = asyncio.get_running_loop()
        events: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def _emit(kind: str, payload: Any = None) -> None:
            if loop.is_closed():
                return
            try:
                loop.call_soon_threadsafe(events.put_nowait, (kind, payload))
            except RuntimeError:
                return

        class _Callback(QwenTtsRealtimeCallback):
            def on_event(self, message) -> None:  # noqa: ANN001
                _emit("event", message)

            def on_close(self, close_status_code, close_msg) -> None:  # noqa: ANN001
                _emit("closed", (close_status_code, close_msg))

        tts = QwenTtsRealtime(
            model=self.model,
            callback=_Callback(),
            workspace=session.workspace_id or None,
            url=session.websocket_url,
        )

        try:
            await asyncio.wait_for(loop.run_in_executor(None, tts.connect), timeout=_CONNECT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as error:
            raise ProviderUnavailable(f"{self.name} websocket connect timed out", provider=self.name) from error
        except TimeoutError as error:
            raise ProviderUnavailable(f"{self.name} websocket connect timed out", provider=self.name) from error

        tts.update_session(voice=self.voice_id, audio_format="pcm", language_type=self.language)

        finished = False

        async def _feed() -> None:
            async for chunk in text:
                context.cancellation.raise_if_cancelled()
                chunk = chunk.strip()
                if chunk:
                    tts.append_text(chunk)
            tts.finish()

        feed_task = asyncio.ensure_future(_feed())
        try:
            while True:
                if context.cancellation.cancelled:
                    raise TurnCancelled()
                try:
                    kind, payload = await asyncio.wait_for(events.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if feed_task.done():
                        feed_task.result()
                    continue
                if kind == "closed":
                    if not finished:
                        code, message = payload
                        raise ProviderUnavailable(
                            f"{self.name} websocket closed before response.done ({code}: {message})",
                            provider=self.name,
                        )
                    return
                message = payload
                message_type = message.get("type")
                if message_type == "response.audio.delta":
                    delta = message.get("delta")
                    if delta:
                        yield base64.b64decode(delta)
                elif message_type == "response.done":
                    finished = True
                elif message_type in ("error", "response.error"):
                    _raise_event_error(message, self.name)
        finally:
            if not feed_task.done():
                feed_task.cancel()
            try:
                tts.close()
            except Exception:  # noqa: BLE001 - already closed or never connected cleanly
                pass


def _raise_event_error(message: dict, provider: str) -> None:
    detail = message.get("error") or message
    text = f"{provider} realtime error: {detail}"
    code = detail.get("code") if isinstance(detail, dict) else None
    if code in ("invalid_request_error", "authentication_error", "invalid_api_key"):
        raise ProviderConfigError(text, provider=provider)
    raise ProviderUnavailable(text, provider=provider)
