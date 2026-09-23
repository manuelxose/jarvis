"""Cloud Alibaba Model Studio (Qwen) realtime streaming STT adapter.

Wraps the official ``dashscope`` SDK's ``audio.asr.Recognition`` (a threading/
callback API, not asyncio) behind the ``SpeechToText`` contract. See
``jarvis.adapters._dashscope`` for region/auth wiring and
``docs/engineering/alibaba-qwen-voice-research.md`` for the sourcing behind
the model id, language kwarg, and error-mapping choices below -- several are
flagged there as needing confirmation against a live account before this
adapter is trusted in production.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from jarvis.core.contracts import Transcript, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable

from .._dashscope import configure_dashscope, dashscope_available

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig

_NON_TRANSIENT_STATUS_CODES = {400, 401, 403, 404}

# ponytail: Recognition's constructor has no explicit `language` parameter;
# extra **kwargs are merged into the task's input params, so this is how
# qwen3-asr-flash-realtime's documented Spanish support is requested. Upgrade
# path: if a live call rejects this kwarg name, check the model's current
# parameter name in the DashScope console and rename it here, in one place.
_LANGUAGE_KWARG = "language_hints"


def alibaba_qwen_stt_available(config: "RuntimeConfig") -> bool:
    """Return whether enough configuration is present to call Alibaba (no network probe)."""
    if not dashscope_available():
        return False
    if not config.stt.api_key:
        return False
    if config.alibaba.region == "singapore" and not config.alibaba.workspace_id:
        return False
    return True


class AlibabaQwenSTT:
    """Transcribe int16 PCM frames through Alibaba's realtime ASR WebSocket."""

    name = "alibaba_qwen"

    def __init__(
        self,
        *,
        api_key: Optional[str],
        model: str,
        language: str,
        sample_rate: int,
        config: "RuntimeConfig",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self._config = config

    @classmethod
    def from_config(cls, config: "RuntimeConfig") -> "AlibabaQwenSTT":
        return cls(
            api_key=config.stt.api_key,
            model=config.alibaba.stt_model,
            language=config.stt.language,
            sample_rate=config.audio.sample_rate,
            config=config,
        )

    async def transcribe(self, audio: AsyncIterator[bytes], context: TurnContext) -> AsyncIterator[Transcript]:
        if not dashscope_available():
            raise ProviderUnavailable("dashscope is not installed; alibaba_qwen STT unavailable", provider=self.name)
        session = configure_dashscope(self._config, api_key=self.api_key, provider=self.name)

        from dashscope.audio.asr import Recognition, RecognitionCallback  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        events: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def _emit(kind: str, payload: Any = None) -> None:
            if loop.is_closed():
                return
            try:
                loop.call_soon_threadsafe(events.put_nowait, (kind, payload))
            except RuntimeError:
                return

        class _Callback(RecognitionCallback):
            def on_event(self, result) -> None:  # noqa: ANN001
                _emit("event", result)

            def on_error(self, result) -> None:  # noqa: ANN001
                _emit("error", result)

            def on_complete(self) -> None:
                _emit("complete")

        recognition = Recognition(
            model=self.model,
            callback=_Callback(),
            format="pcm",
            sample_rate=self.sample_rate,
            workspace=session.workspace_id or None,
            **{_LANGUAGE_KWARG: [self.language]},
        )
        recognition.start()

        async def _feed() -> None:
            async for chunk in audio:
                context.cancellation.raise_if_cancelled()
                recognition.send_audio_frame(chunk)

        feed_task = asyncio.ensure_future(_feed())
        try:
            while True:
                context.cancellation.raise_if_cancelled()
                try:
                    kind, payload = await asyncio.wait_for(events.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if feed_task.done():
                        feed_task.result()  # re-raise a feed-side failure, if any
                    continue
                if kind == "complete":
                    return
                if kind == "error":
                    _raise_recognition_error(payload, self.name)
                transcript = _to_transcript(payload)
                if transcript is not None:
                    yield transcript
        finally:
            if not feed_task.done():
                feed_task.cancel()
            try:
                recognition.stop()
            except Exception:  # noqa: BLE001 - already stopped or never started cleanly
                pass


def _to_transcript(result: Any) -> Optional[Transcript]:
    sentence = result.get_sentence() if hasattr(result, "get_sentence") else None
    if not sentence or not isinstance(sentence, dict):
        return None
    text = sentence.get("text", "").strip()
    if not text:
        return None
    is_final = bool(sentence.get("end_time") is not None)
    return Transcript(text, is_final=is_final)


def _raise_recognition_error(result: Any, provider: str) -> None:
    status_code = getattr(result, "status_code", None)
    message = f"{provider} recognition error: {getattr(result, 'message', result)}"
    if status_code in _NON_TRANSIENT_STATUS_CODES:
        raise ProviderConfigError(message, provider=provider)
    raise ProviderUnavailable(message, provider=provider)
