"""Compose the full Jarvis runtime from real or fake adapters.

The runtime wires the supervisor (lifecycle/health), the turn manager
(routing/cancellation), and all cross-cutting services. Real adapters are used
by default and degrade cleanly when dependencies or credentials are missing;
``use_fakes=True`` produces a deterministic, fully-healthy runtime for offline
acceptance and benchmarking.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from jarvis.adapters.audio import EnergyVAD
from jarvis.adapters.audio.activation import ActivationManager
from jarvis.adapters.audio.input import MicCapture
from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
from jarvis.adapters.fakes import (
    EchoTTS,
    RecordingAudioPlayer,
    ScriptedAudioInput,
    ScriptedHermes,
    ScriptedMemory,
    ScriptedModel,
    ScriptedSTT,
)
from jarvis.adapters.hermes.child import HermesChildAdapter
from jarvis.adapters.memory.service import MemoryService
from jarvis.adapters.memory.store import MemoryStore
from jarvis.adapters.models.fallback import ProviderChain
from jarvis.adapters.models.ollama import FALLBACK_KEEP_ALIVE, KEEP_ALIVE, OllamaProvider
from jarvis.adapters.models.openai_compat import OpenAICompatProvider
from jarvis.adapters.stt import resolve_stt, stt_available, stt_provider
from jarvis.adapters.stt.fallback import STTChain
from jarvis.adapters.stt.whisper import WhisperSTT
from jarvis.adapters.tts import AckAudioCache, resolve_tts, tts_available, tts_provider
from jarvis.adapters.tts.fallback import TTSChain
from jarvis.adapters.tts.pyttsx3 import Pyttsx3TTS
from jarvis.adapters.tts.qwen_clone import QwenCloneTTS
from jarvis.core.circuit_breaker import CircuitBreaker
from jarvis.adapters.tools.gateway import Risk, Tool, ToolGateway
from jarvis.adapters.tools.windows import build_windows_tools
from jarvis.config import RuntimeConfig
from jarvis.core.contracts import (
    HealthReport,
    HealthStatus,
    ManagedComponent,
    ModelProvider,
    Transcript,
    TurnContext,
)
from jarvis.core.lifecycle import Supervisor
from jarvis.core.state import RuntimeState
from jarvis.observability.metrics import LatencyMetrics

from .routing import Router
from .turn_manager import TurnManager, TurnResult
from .voice_loop import VoiceLoop


@dataclass
class RuntimeComponents:
    memory: Optional[MemoryService]
    model: ModelProvider
    hermes: Any
    tools: ToolGateway
    audio_output: AudioOutputQueue
    turn_manager: TurnManager
    voice_loop: VoiceLoop


class JarvisRuntime:
    """The launchable Jarvis application runtime."""

    def __init__(
        self,
        config: RuntimeConfig,
        supervisor: Supervisor,
        components: RuntimeComponents,
        *,
        activation: ActivationManager,
        voice_loop: VoiceLoop,
        metrics: LatencyMetrics | None = None,
    ) -> None:
        self.config = config
        self.supervisor = supervisor
        self.components = components
        self.activation = activation
        self.voice_loop = voice_loop
        self.metrics = metrics or LatencyMetrics()

    @property
    def state(self) -> RuntimeState:
        return self.supervisor.state

    async def start(self) -> None:
        await self.supervisor.start()

    async def stop(self) -> None:
        self.voice_loop.request_stop()
        await self.supervisor.stop()

    async def run_until_stopped(self, max_turns: int | None = None) -> None:
        await self.supervisor.start()
        # Fire-and-forget: listening starts immediately while models load.
        warm_up = asyncio.create_task(
            asyncio.to_thread(_warm_up, self.components.model, getattr(self.voice_loop, "_stt", None))
        )
        try:
            await self.voice_loop.run(max_turns=max_turns)
        finally:
            warm_up.cancel()
            await self.supervisor.stop()

    async def handle(self, text: str, conversation_id: str = "demo") -> TurnResult:
        started = time.monotonic()
        result = await self.components.turn_manager.handle(text, conversation_id=conversation_id)
        elapsed_ms = (time.monotonic() - started) * 1000
        self.metrics.record("turn_latency_ms", elapsed_ms)
        self.metrics.record("speech_end_to_first_audio_ms", elapsed_ms)
        return result

    async def interrupt(self) -> None:
        await self.components.turn_manager.interrupt()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "model": _model_diagnostics(self.components.model),
            "components": {
                report.name: {
                    "status": report.status.value,
                    "detail": report.detail,
                    "required": report.required,
                }
                for report in self.supervisor.health_snapshot()
            },
            "metrics": self.metrics.summary(),
            "audio_queue": self.components.audio_output.state(),
            "voice_loop": self.voice_loop.state(),
            "tts": _tts_diagnostics(self.components.turn_manager),
        }


def _tts_diagnostics(turn_manager: Any) -> dict[str, Any]:
    tts = getattr(turn_manager, "_tts", None)
    providers = getattr(tts, "providers", (tts,))
    result: dict[str, Any] = {"providers": [getattr(p, "name", type(p).__name__) for p in providers]}
    for provider in providers:
        if isinstance(provider, QwenCloneTTS):
            result["voice_clone"] = provider.state()
    return result


def build_runtime(
    config: RuntimeConfig,
    *,
    use_fakes: bool = False,
    fake_stt: Any = None,
) -> JarvisRuntime:
    """Compose the runtime. ``use_fakes=True`` yields a fully-healthy offline runtime.

    ``fake_stt`` overrides the scripted STT transcript when ``use_fakes=True``,
    so the hardware-acceptance harness can drive a specific utterance through
    the voice loop without audio hardware or provider credentials.
    """
    if use_fakes:
        return _build_fake_runtime(config, stt=fake_stt)
    return _build_real_runtime(config)


def _build_real_runtime(config: RuntimeConfig) -> JarvisRuntime:
    memory = _build_memory(config)
    model = _build_model_chain(config)
    hermes = HermesChildAdapter(
        list(config.hermes.command) if config.hermes.command else None,
        timeout_seconds=config.hermes.timeout_seconds,
        restart_max=config.hermes.restart_max,
    )
    tools = _build_tools(config, confirmer=None)
    # Chunks are written in short slices, so a long SAPI sentence needs a
    # timeout above its duration rather than the 5 s per-chunk default.
    audio_output = AudioOutputQueue(
        render=make_sounddevice_render(device=config.audio.output_device), render_timeout_seconds=60.0
    )
    tts = _build_tts_chain(config)
    stt = _build_stt_chain(config)
    audio_input = MicCapture(
        sample_rate=config.audio.sample_rate,
        channels=config.audio.channels,
        device=config.audio.input_device,
    )

    router = Router()
    turn_manager = TurnManager(
        router=router,
        tools=tools.execute,
        model=model,
        tts=tts,
        audio=audio_output,
        hermes=hermes,
        memory=memory,
        ack_cache=AckAudioCache(Path("cache/tts_acks")),
    )

    health_components = _real_health_components(config, memory, model, hermes, audio_input, audio_output)
    voice_clone = next((p for p in getattr(tts, "providers", (tts,)) if isinstance(p, QwenCloneTTS)), None)
    if voice_clone is not None:
        # Supervised worker: started with the runtime (non-blocking; SAPI speaks
        # until the model is warm) and stopped with it.
        health_components.append(voice_clone)
    supervisor = Supervisor(health_components)
    activation = ActivationManager(
        mode=config.activation.mode,
        wake_word=config.activation.wake_word,
        cooldown_seconds=config.activation.cooldown_seconds,
        conversation_timeout_seconds=config.activation.conversation_timeout_seconds,
    )
    vad = EnergyVAD()
    voice_loop = VoiceLoop(
        audio=audio_input,
        vad=vad,
        stt=stt,
        turn_manager=turn_manager,
        activation=activation,
        # ponytail: barge-in is opt-in (audio.barge_in) — without acoustic echo
        # cancellation Jarvis's own voice from the speakers cancels its reply.
        # Upgrade path: AEC (e.g. WebRTC APM), then default it on.
        barge_in_frames=5 if config.audio.barge_in else 0,
        echo_tail_frames=3,
    )
    components = RuntimeComponents(memory, model, hermes, tools, audio_output, turn_manager, voice_loop)
    return JarvisRuntime(config, supervisor, components, activation=activation, voice_loop=voice_loop)


def _build_fake_runtime(config: RuntimeConfig, *, stt: Any = None) -> JarvisRuntime:
    injected_stt = stt is not None
    memory = _build_memory(config)
    model = ScriptedModel({"": "Respuesta de demostracion."}, default="Respuesta de demostracion.")
    hermes = ScriptedHermes()
    tools = _build_tools(config, confirmer=_auto_approve)
    audio_output = AudioOutputQueue(render=_recording_render([]))
    tts = EchoTTS()
    stt = stt or ScriptedSTT([Transcript("Jarvis, hola", is_final=True)])
    audio_input = ScriptedAudioInput(
        frames=[b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6
    )

    router = Router()
    turn_manager = TurnManager(
        router=router,
        tools=tools.execute,
        model=model,
        tts=tts,
        audio=audio_output,
        hermes=hermes,
        memory=memory,
    )

    health_components = _fake_health_components(config, memory)
    supervisor = Supervisor(health_components)
    activation = ActivationManager(
        mode="continuous" if injected_stt else config.activation.mode,
        wake_word=config.activation.wake_word,
        cooldown_seconds=config.activation.cooldown_seconds,
        conversation_timeout_seconds=config.activation.conversation_timeout_seconds,
    )
    vad = EnergyVAD()
    voice_loop = VoiceLoop(
        audio=audio_input,
        vad=vad,
        stt=stt,
        turn_manager=turn_manager,
        activation=activation,
    )
    components = RuntimeComponents(memory, model, hermes, tools, audio_output, turn_manager, voice_loop)
    return JarvisRuntime(config, supervisor, components, activation=activation, voice_loop=voice_loop)


def _build_memory(config: RuntimeConfig) -> MemoryService:
    store = MemoryStore(config.memory.db_path)
    return MemoryService(store, max_recall=config.memory.max_recall)


def _build_tts_chain(config: RuntimeConfig):
    """Resolve the configured TTS provider, with a local fallback when cloud is primary."""
    primary = resolve_tts(config)
    if tts_provider(config) not in ("alibaba_qwen", "qwen_clone"):
        return primary
    # The breaker probes the clone again every few seconds, so the owner's voice
    # takes over as soon as the worker finishes warming up.
    return TTSChain([primary, Pyttsx3TTS()], breaker=CircuitBreaker(cooldown_seconds=5.0))


def _build_stt_chain(config: RuntimeConfig):
    """Resolve the configured STT provider, with a local fallback when cloud is primary."""
    primary = resolve_stt(config)
    if stt_provider(config) != "alibaba_qwen":
        return primary
    fallback = WhisperSTT(
        model=config.stt.model,
        language=config.stt.language,
        device=config.stt.device,
        sample_rate=config.audio.sample_rate,
        hotwords=config.activation.wake_word,
    )
    return STTChain([primary, fallback])


def _build_model_chain(config: RuntimeConfig) -> ModelProvider:
    from jarvis.observability.cost import ProviderRate, SpendLedger  # noqa: PLC0415

    ledger = SpendLedger(_data_dir() / "spend.json", config.models.max_daily_usd)
    providers: list[ModelProvider] = []
    for spec in config.models.providers:
        if spec.kind == "ollama":
            providers.append(
                OllamaProvider(
                    base_url=spec.base_url or "http://127.0.0.1:11434",
                    model=spec.model,
                    timeout_seconds=spec.timeout_seconds,
                    temperature=spec.temperature,
                    name=spec.name or "ollama",
                    keep_alive=KEEP_ALIVE if not providers else FALLBACK_KEEP_ALIVE,
                )
            )
        else:
            providers.append(
                OpenAICompatProvider(
                    base_url=spec.base_url or "",
                    model=spec.model,
                    api_key=spec.api_key,
                    timeout_seconds=spec.timeout_seconds,
                    temperature=spec.temperature,
                    name=spec.name or "openai_compat",
                    rate=ProviderRate(spec.input_usd_per_million, spec.output_usd_per_million),
                    ledger=ledger,
                    extra_body=dict(spec.extra_body),
                )
            )
    return ProviderChain(providers)


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "jarvis"


def _model_diagnostics(
    model: ModelProvider, probe: Callable[[str], bool] | None = None
) -> dict[str, Any]:
    """Describe the resolved model chain without exposing credential material.

    The resolved primary and ordered fallbacks are emitted with name, kind,
    model, base URL, and API-key presence only; the key value itself is never
    included. Local fallback readiness is computed with the existing bounded
    Ollama probe so ``jarvis doctor`` reports truthfully without probing any
    cloud endpoint (D014).
    """
    if not isinstance(model, ProviderChain):
        return {
            "provider_order": [],
            "providers": [],
            "primary": None,
            "fallbacks": [],
            "local_fallback_ready": False,
        }
    reachable = probe or _ollama_reachable
    providers: list[dict[str, Any]] = []
    for provider in model.providers:
        if isinstance(provider, OllamaProvider):
            kind = "ollama"
        elif isinstance(provider, OpenAICompatProvider):
            kind = "openai_compat"
        else:
            kind = type(provider).__name__
        providers.append(
            {
                "name": getattr(provider, "name", kind),
                "kind": kind,
                "model": getattr(provider, "model", ""),
                "base_url": getattr(provider, "base_url", None),
                "has_api_key": bool(getattr(provider, "api_key", None)),
            }
        )
        ledger = getattr(provider, "ledger", None)
        if ledger is not None:
            rate = getattr(provider, "rate", None)
            providers[-1]["priced"] = bool(rate and (rate.input_token_per_million or rate.output_token_per_million))
            providers[-1]["spent_today_usd"] = ledger.spent_today()
            providers[-1]["max_daily_usd"] = ledger.max_daily_usd
    ollama_providers = [
        provider
        for provider in model.providers
        if isinstance(provider, OllamaProvider) and provider.base_url
    ]
    local_fallback_ready = bool(ollama_providers) and any(
        reachable(provider.base_url) for provider in ollama_providers
    )
    return {
        "provider_order": [provider["name"] for provider in providers],
        "providers": providers,
        "primary": providers[0] if providers else None,
        "fallbacks": providers[1:],
        "local_fallback_ready": local_fallback_ready,
    }


def _build_tools(config: RuntimeConfig, confirmer: Any) -> ToolGateway:
    return ToolGateway(
        build_windows_tools(),
        allowlist=config.tools.allowlist if config.tools.allowlist else None,
        confirmer=confirmer,
        confirmation_timeout_seconds=config.tools.confirmation_timeout_seconds,
    )


async def _auto_approve(name: str, arguments: Any, context: TurnContext) -> bool:
    return True


def _recording_render(recorder: list) -> Callable[[str, bytes], Any]:
    async def _render(turn_id: str, chunk: bytes) -> None:
        recorder.append((turn_id, chunk))

    return _render


def _storage_report(config: RuntimeConfig) -> HealthReport:
    path = Path(config.memory.db_path).parent
    ok = path.is_dir() and os.access(path, os.W_OK)
    return HealthReport(
        "storage path",
        HealthStatus.HEALTHY if ok else HealthStatus.FAILED,
        "" if ok else "storage path is unavailable",
        required=True,
    )


def _stt_report(config: RuntimeConfig) -> HealthReport:
    provider = stt_provider(config)
    available = stt_available(config)
    if provider == "sapi":
        healthy_detail = "sapi adapter"
        degraded_detail = "sapi adapter unavailable (comtypes)"
    elif provider == "alibaba_qwen":
        healthy_detail = "alibaba_qwen (cloud, faster-whisper fallback)"
        degraded_detail = "alibaba_qwen api_key or workspace_id not configured"
    else:
        healthy_detail = "whisper (faster-whisper)"
        degraded_detail = "adapter dependency unavailable"
    if available:
        return HealthReport("STT", HealthStatus.HEALTHY, healthy_detail)
    return HealthReport("STT", HealthStatus.DEGRADED, degraded_detail, required=False)


def _tts_report(config: RuntimeConfig) -> HealthReport:
    provider = tts_provider(config)
    available = tts_available(config)
    if provider == "sapi":
        healthy_detail = "pyttsx3 adapter"
        degraded_detail = "pyttsx3 adapter unavailable"
    elif provider == "alibaba_qwen":
        healthy_detail = "alibaba_qwen (cloud, cloned voice, sapi fallback)"
        degraded_detail = "alibaba_qwen api_key, voice, or workspace_id not configured"
    elif provider == "qwen_clone":
        healthy_detail = "qwen_clone (local Faster Qwen3-TTS worker, sapi fallback)"
        degraded_detail = "TTS worker venv missing (run scripts\\setup_tts_worker.bat); sapi fallback"
    else:
        healthy_detail = "coqui (XTTS-v2)"
        degraded_detail = "adapter dependency unavailable"
    if available:
        return HealthReport("TTS", HealthStatus.HEALTHY, healthy_detail)
    return HealthReport("TTS", HealthStatus.DEGRADED, degraded_detail, required=False)


def _warm_up(model: Any, stt: Any) -> None:
    """Best-effort preload of local models so the first turn skips cold loads."""
    # Only a primary local model is preloaded; a fallback loads on demand.
    primary = next(iter(getattr(model, "providers", ())), None)
    if isinstance(primary, OllamaProvider):
        primary.warm_up()
    for provider in getattr(stt, "providers", (stt,)):
        if isinstance(provider, WhisperSTT):
            try:
                provider._load_model()
            except Exception:  # noqa: BLE001 - surfaced again on the first real turn
                pass


def _ollama_reachable(base_url: str, timeout_seconds: float = 1.5) -> bool:
    """Best-effort synchronous probe of an Ollama ``/api/version`` endpoint.

    Returns True only for a sub-400 HTTP status. Connection, DNS, timeout, and
    malformed-URL errors all resolve to False so a configured-but-down Ollama
    reads degraded rather than healthy.
    """
    import urllib.error
    import urllib.request

    url = f"{base_url.rstrip('/')}/api/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return response.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _real_health_components(
    config: RuntimeConfig,
    memory: MemoryService,
    model: ModelProvider,
    hermes: HermesChildAdapter,
    audio_input: MicCapture,
    audio_output: AudioOutputQueue,
) -> list[ManagedComponent]:
    def _model_report() -> HealthReport:
        chain = model if isinstance(model, ProviderChain) else None
        count = len(chain.providers) if chain else 0
        if not count:
            return HealthReport("fast model", HealthStatus.DEGRADED, "no model providers configured", required=False)
        ollama_providers = [
            provider
            for provider in (chain.providers if chain else ())
            if isinstance(provider, OllamaProvider) and provider.base_url
        ]
        if ollama_providers and not any(
            _ollama_reachable(provider.base_url) for provider in ollama_providers
        ):
            return HealthReport("fast model", HealthStatus.DEGRADED, "Ollama unreachable", required=False)
        return HealthReport("fast model", HealthStatus.HEALTHY, f"{count} provider(s)")

    return [
        _StaticComponent("configuration", HealthReport("configuration", HealthStatus.HEALTHY, "configuration loaded"), required=True),
        _StaticComponent("storage path", _storage_report(config), required=True),
        _StaticComponent(
            "memory",
            HealthReport("memory", HealthStatus.HEALTHY, "SQLite store available"),
            required=False,
            stop=memory.close,
        ),
        _StaticComponent("audio input", _availability_report("audio input", _audio_available()), required=False),
        _StaticComponent(
            "audio output",
            _availability_report(
                "audio output",
                _audio_output_available(),
                detail="no PortAudio output device available",
            ),
            required=False,
        ),
        _StaticComponent("STT", _stt_report(config), required=False),
        _StaticComponent("TTS", _tts_report(config), required=False),
        _StaticComponent("fast model", _model_report(), required=False),
        _StaticComponent(
            "Hermes",
            _availability_report("Hermes", bool(config.hermes.command), detail="agent workflows unavailable"),
            required=False,
        ),
        _StaticComponent("network", HealthReport("network", HealthStatus.DEGRADED, "not probed", required=False), required=False),
    ]


def _fake_health_components(config: RuntimeConfig, memory: MemoryService) -> list[ManagedComponent]:
    return [
        _StaticComponent("configuration", HealthReport("configuration", HealthStatus.HEALTHY, "configuration loaded"), required=True),
        _StaticComponent("storage path", _storage_report(config), required=True),
        _StaticComponent("memory", HealthReport("memory", HealthStatus.HEALTHY, "SQLite store available"), required=False, stop=memory.close),
        _StaticComponent("audio input", HealthReport("audio input", HealthStatus.HEALTHY, "fake capture"), required=False),
        _StaticComponent("audio output", HealthReport("audio output", HealthStatus.HEALTHY, "fake playback"), required=False),
        _StaticComponent("STT", HealthReport("STT", HealthStatus.HEALTHY, "fake STT"), required=False),
        _StaticComponent("TTS", HealthReport("TTS", HealthStatus.HEALTHY, "fake TTS"), required=False),
        _StaticComponent("fast model", HealthReport("fast model", HealthStatus.HEALTHY, "fake model"), required=False),
        _StaticComponent("Hermes", HealthReport("Hermes", HealthStatus.HEALTHY, "fake agent"), required=False),
        _StaticComponent("network", HealthReport("network", HealthStatus.HEALTHY, "fake network"), required=False),
    ]


def _availability_report(name: str, available: bool, detail: str = "adapter dependency unavailable") -> HealthReport:
    if available:
        return HealthReport(name, HealthStatus.HEALTHY)
    return HealthReport(name, HealthStatus.DEGRADED, detail, required=False)


def _sounddevice():
    """Return the sounddevice module, or None when the package/PortAudio is missing.

    A sounddevice install without the PortAudio shared library raises OSError at
    import, so that must read as "unavailable" too rather than crash doctor.
    """
    try:
        import sounddevice as sd  # noqa: PLC0415

        return sd
    except (ImportError, OSError):
        return None


def _audio_available() -> bool:
    return _sounddevice() is not None


def _audio_output_available() -> bool:
    """Probe a real output device so doctor cannot report a static HEALTHY."""
    sd = _sounddevice()
    if sd is None:
        return False
    try:
        sd.query_devices(kind="output")
    except Exception:  # noqa: BLE001 - a probe that raises cannot confirm output either
        return False
    return True


class _StaticComponent:
    """Adapt a static health report (plus optional stop hook) to ManagedComponent."""

    def __init__(
        self,
        name: str,
        report: HealthReport,
        *,
        required: bool,
        start: Optional[Callable[[], Any]] = None,
        stop: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.name = name
        self.required = required
        self._report = report
        self._start = start
        self._stop = stop

    async def start(self) -> None:
        if self._start is not None:
            result = self._start()
            import asyncio

            if asyncio.iscoroutine(result):
                await result

    async def stop(self) -> None:
        if self._stop is not None:
            result = self._stop()
            import asyncio

            if asyncio.iscoroutine(result):
                await result

    async def health(self) -> HealthReport:
        return self._report
