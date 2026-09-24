"""Cinematic startup: chime -> music -> concurrent init -> truthful welcome -> listen.

The sequence is a small state machine owned by :class:`StartupSequence`:

    idle -> acknowledged -> initializing -> announcing -> ready | degraded
                         \\-> cancelled (any time)            \\-> failed

It is idempotent (a second trigger while running joins the running sequence;
after completion it is a no-op until :meth:`reset`), interruptible
(:meth:`cancel` stops music and pending waits; launched apps keep running),
and it never says "all systems operational" unless every essential component
reported healthy. Every dependency is injected, so the orchestration is tested
without audio hardware, GPUs or Windows.
"""

from __future__ import annotations

import asyncio
import datetime
import enum
import logging
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

from jarvis.core.contracts import HealthReport, HealthStatus

logger = logging.getLogger("jarvis.startup")

DEFAULT_WELCOME = (
    "{greeting}, {name}. Todos los sistemas están operativos. "
    "Preparando tu entorno de trabajo. ¿En qué puedo ayudarte?"
)
DEFAULT_DEGRADED = (
    "{greeting}, {name}. Estoy en marcha, pero con limitaciones: {issues}. "
    "Preparando tu entorno de trabajo. ¿En qué puedo ayudarte?"
)
REFERENCE_MUSIC_URL = "https://www.youtube.com/watch?v=BN1WwnEDWAM"

# Spoken names for health components (the supervisor uses English ids).
_SPOKEN = {
    "STT": "el reconocimiento de voz",
    "TTS": "la síntesis de voz",
    "TTS voice clone": "mi voz clonada",
    "fast model": "el modelo de lenguaje",
    "Hermes": "Hermes",
    "memory": "la memoria",
    "audio input": "el micrófono",
    "audio output": "el altavoz",
    "storage path": "el almacenamiento",
    "configuration": "la configuración",
}


class StartupPhase(str, enum.Enum):
    IDLE = "idle"
    ACKNOWLEDGED = "acknowledged"
    INITIALIZING = "initializing"
    ANNOUNCING = "announcing"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_DONE = {StartupPhase.READY, StartupPhase.DEGRADED, StartupPhase.FAILED}


@dataclass(frozen=True)
class StartupOptions:
    owner_name: str = "Manuel"
    welcome: str = DEFAULT_WELCOME
    degraded: str = DEFAULT_DEGRADED
    # Optional per-period overrides: {"morning": ..., "afternoon": ..., "evening": ...}
    welcome_variants: dict[str, str] = field(default_factory=dict)
    music_path: str = ""
    music_url: str = ""  # opened in the browser only when no local file is usable
    music_volume: float = 0.55
    duck_volume: float = 0.12
    # after_welcome="restore": the music continues at this background level. Louder
    # music masks the owner's voice for the microphone (measured on the laptop).
    background_volume: float = 0.10
    fade_in_seconds: float = 1.5
    duck_seconds: float = 0.35
    fade_out_seconds: float = 2.5
    # A pre-recorded welcome (cloned voice) lands this long after the music starts.
    welcome_delay_seconds: float = 2.0
    after_welcome: str = "fade"  # fade | restore
    activation_sound: str = ""  # empty = synthesized chime
    services_timeout_seconds: float = 20.0
    # A live (degraded) welcome waits this long for the cloned voice, then the
    # truthful warning is spoken in the fallback voice instead.
    voice_ready_timeout_seconds: float = 8.0
    # Hard cap on the whole announcement: speech can never block the session.
    announce_timeout_seconds: float = 30.0
    # Components that must be HEALTHY for the "all systems operational" line.
    essential: tuple[str, ...] = ("configuration", "storage path", "audio input", "audio output", "STT", "TTS", "fast model")


@dataclass
class StartupReport:
    trigger: str = ""
    phase: StartupPhase = StartupPhase.IDLE
    welcome: str = ""
    issues: list[str] = field(default_factory=list)
    music: str = "none"  # file | url | missing | error | none
    welcome_source: str = "live"  # live | cache | fallback
    timings_ms: dict[str, float] = field(default_factory=dict)
    workspace: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "phase": self.phase.value,
            "welcome": self.welcome,
            "issues": list(self.issues),
            "music": self.music,
            "welcome_source": self.welcome_source,
            "timings_ms": {k: round(v, 1) for k, v in self.timings_ms.items()},
            "workspace": self.workspace,
        }


def greeting_for(now: datetime.datetime) -> tuple[str, str]:
    """Return (period, Spanish greeting) for the local time."""
    if 6 <= now.hour < 12:
        return "morning", "Buenos días"
    if 12 <= now.hour < 21:
        return "afternoon", "Buenas tardes"
    return "evening", "Buenas noches"


def compose_welcome(
    reports: Iterable[HealthReport],
    options: StartupOptions,
    now: datetime.datetime,
    *,
    voice_ready: bool = True,
) -> tuple[str, list[str]]:
    """Build the spoken welcome; returns (text, issues). Issues empty = all good."""
    issues: list[str] = []
    for report in reports:
        failed_required = report.required and report.status is HealthStatus.FAILED
        essential_unhealthy = report.name in options.essential and report.status is not HealthStatus.HEALTHY
        if failed_required or essential_unhealthy:
            issues.append(_SPOKEN.get(report.name, report.name))
    if not voice_ready:
        issues.append(_SPOKEN["TTS voice clone"])
    period, greeting = greeting_for(now)
    values = {"greeting": greeting, "name": options.owner_name}
    if issues:
        unique = list(dict.fromkeys(issues))
        spoken = unique[0] if len(unique) == 1 else ", ".join(unique[:-1]) + " y " + unique[-1]
        return options.degraded.format(issues=f"{spoken} no {'está' if len(unique) == 1 else 'están'} disponible{'s' if len(unique) > 1 else ''}", **values), unique
    template = options.welcome_variants.get(period) or options.welcome
    return template.format(**values), []


def welcome_texts(options: StartupOptions) -> list[str]:
    """The 'all operational' welcome for each period (the cacheable ones)."""
    healthy = [HealthReport(name, HealthStatus.HEALTHY) for name in options.essential]
    return [compose_welcome(healthy, options, datetime.datetime(2026, 1, 1, hour))[0] for hour in (8, 15, 22)]


class StartupSequence:
    """Run the startup experience once per activation; see module docstring."""

    def __init__(
        self,
        options: StartupOptions,
        *,
        mixer: Any = None,
        speak: Callable[[str], Awaitable[None]],
        start_services: Callable[[], Awaitable[list[HealthReport]]],
        wait_voice: Optional[Callable[[], Awaitable[bool]]] = None,
        start_workspace: Optional[Callable[[], Awaitable[Any]]] = None,
        on_phase: Optional[Callable[[StartupPhase, dict[str, Any]], None]] = None,
        cached_welcome: Optional[Callable[[str], Optional[bytes]]] = None,
        play_audio: Optional[Callable[[bytes], Awaitable[None]]] = None,
        speak_fallback: Optional[Callable[[str], Awaitable[None]]] = None,
        open_url: Callable[[str], Any] = webbrowser.open,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
    ) -> None:
        self.options = options
        self._mixer = mixer
        self._speak = speak
        self._start_services = start_services
        self._wait_voice = wait_voice
        self._start_workspace = start_workspace
        self._on_phase = on_phase
        self._cached_welcome = cached_welcome
        self._play_audio = play_audio
        self._speak_fallback = speak_fallback
        self._open_url = open_url
        self._clock = clock
        self._task: Optional[asyncio.Task[StartupReport]] = None
        self.workspace_task: Optional[asyncio.Task[Any]] = None
        self.report = StartupReport()

    @property
    def phase(self) -> StartupPhase:
        return self.report.phase

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def trigger(self, source: str = "manual") -> StartupReport:
        """Start (or join) the sequence. A completed sequence is not repeated."""
        if self.running:
            logger.info("startup already running; ignoring %s trigger", source)
            return await asyncio.shield(self._task)
        if self.phase in _DONE:
            logger.info("startup already complete (%s); ignoring %s trigger", self.phase.value, source)
            return self.report
        self.report = StartupReport(trigger=source)
        self._task = asyncio.create_task(self._run())
        return await asyncio.shield(self._task)

    def reset(self) -> None:
        """Allow a new activation (after Jarvis went back to sleep)."""
        if not self.running:
            self.report = StartupReport()

    async def cancel(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._mixer is not None:
            self._mixer.fade_out(0.4)
        self._set_phase(StartupPhase.CANCELLED)

    # -- internals ----------------------------------------------------------
    def _set_phase(self, phase: StartupPhase, **detail: Any) -> None:
        self.report.phase = phase
        logger.info("startup phase: %s %s", phase.value, detail or "")
        if self._on_phase is not None:
            try:
                self._on_phase(phase, detail)
            except Exception:  # noqa: BLE001 - observers must not break startup
                logger.debug("startup observer failed", exc_info=True)

    def _mark(self, name: str, started: float) -> None:
        self.report.timings_ms[name] = (time.perf_counter() - started) * 1000

    async def _run(self) -> StartupReport:
        started = time.perf_counter()
        options = self.options
        try:
            self._set_phase(StartupPhase.ACKNOWLEDGED)
            self._play_activation_sound()
            self._mark("first_sound", started)

            services = asyncio.create_task(self._start_services())
            if self._start_workspace is not None:
                self.workspace_task = asyncio.create_task(self._run_workspace(started))
            # Decoding the track can take a moment; it must not delay the chime or init.
            await self._start_music()
            self._mark("music_started", started)
            self._set_phase(StartupPhase.INITIALIZING)

            try:
                reports = await asyncio.wait_for(services, options.services_timeout_seconds)
            except asyncio.TimeoutError:
                reports = [HealthReport("configuration", HealthStatus.FAILED, "startup timed out")]
            except Exception as error:  # noqa: BLE001 - reported, not raised
                logger.warning("service startup failed: %s", error)
                reports = [HealthReport("configuration", HealthStatus.FAILED, str(error))]
            self._mark("services_ready", started)

            text, issues = compose_welcome(reports, options, self._clock())
            cached = self._cached_welcome(text) if (not issues and self._cached_welcome and self._play_audio) else None
            if cached is not None:
                # Movie timing: the recorded cloned-voice welcome needs no model,
                # so it lands shortly after the music instead of after the load.
                self.report.welcome_source = "cache"
                music_at = self.report.timings_ms.get("music_started", 0.0) / 1000
                # Start ducking early so the voice itself lands on the delay mark.
                duck = options.duck_seconds if getattr(self._mixer, "music_playing", False) else 0.0
                await asyncio.sleep(max(0.0, music_at + options.welcome_delay_seconds - duck - (time.perf_counter() - started)))
                speak = lambda: self._play_audio(cached)  # noqa: E731
            else:
                voice_ready = True
                if self._wait_voice is not None:
                    try:
                        voice_ready = bool(await asyncio.wait_for(self._wait_voice(), options.voice_ready_timeout_seconds))
                    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                        voice_ready = False
                self._mark("voice_ready", started)
                text, issues = compose_welcome(reports, options, self._clock(), voice_ready=voice_ready)
                use_fallback = not voice_ready and self._speak_fallback is not None
                self.report.welcome_source = "fallback" if use_fallback else "live"
                speak = (lambda: self._speak_fallback(text)) if use_fallback else (lambda: self._speak(text))  # noqa: E731
            self.report.welcome, self.report.issues = text, issues
            self._set_phase(StartupPhase.ANNOUNCING, issues=issues)
            await self._announce(speak)
            self._mark("welcome_spoken", started)

            required_failed = any(r.required and r.status is HealthStatus.FAILED for r in reports)
            final = StartupPhase.FAILED if required_failed else StartupPhase.DEGRADED if issues else StartupPhase.READY
            self._set_phase(final, issues=issues)
            self._mark("interactive", started)
            return self.report
        except asyncio.CancelledError:
            if self._mixer is not None:
                self._mixer.fade_out(0.4)
            self.report.phase = StartupPhase.CANCELLED
            raise

    def _play_activation_sound(self) -> None:
        if self._mixer is None:
            return
        try:
            samples = None
            if self.options.activation_sound:
                import soundfile as sf  # noqa: PLC0415

                samples, _ = sf.read(self.options.activation_sound, dtype="float32", always_2d=True)
                if samples.shape[1] == 1:
                    samples = samples.repeat(2, axis=1)
            self._mixer.play_sfx(samples)
        except Exception as error:  # noqa: BLE001 - a missing cue must not stop startup
            logger.warning("activation sound unavailable: %s", error)
            try:
                self._mixer.play_sfx(None)
            except Exception:  # noqa: BLE001 - no audio device at all
                pass

    async def _start_music(self) -> None:
        options = self.options
        if options.music_path and self._mixer is not None:
            try:
                await asyncio.to_thread(self._mixer.load, options.music_path)
                self._mixer.play_music(options.music_volume, options.fade_in_seconds)
                self.report.music = "file"
                return
            except FileNotFoundError:
                self.report.music = "missing"
                logger.warning("startup music file not found: %s", options.music_path)
            except Exception as error:  # noqa: BLE001 - bad media or no device
                self.report.music = "error"
                logger.warning("startup music unavailable: %s", error)
        if options.music_url:
            # No downloading: the browser plays the owner's link (no ducking possible).
            try:
                await asyncio.to_thread(self._open_url, options.music_url)
                self.report.music = "url"
            except Exception as error:  # noqa: BLE001
                logger.warning("could not open music url: %s", error)

    async def _announce(self, speak: Callable[[], Awaitable[None]]) -> None:
        mixer, options = self._mixer, self.options
        ducked = mixer is not None and getattr(mixer, "music_playing", False)
        if ducked:
            mixer.ramp(options.duck_volume, options.duck_seconds)
            await asyncio.sleep(options.duck_seconds)
        try:
            await asyncio.wait_for(speak(), options.announce_timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("welcome speech timed out after %.0f s", options.announce_timeout_seconds)
            self.report.issues.append("speech timed out")
        except Exception as error:  # noqa: BLE001 - still finish the sequence
            logger.warning("welcome speech failed: %s", error)
            self.report.issues.append("speech failed")
        finally:
            if ducked:
                if options.after_welcome == "restore":
                    mixer.ramp(options.background_volume, options.duck_seconds * 2)
                else:
                    mixer.fade_out(options.fade_out_seconds)

    async def _run_workspace(self, started: float) -> Any:
        try:
            result = await self._start_workspace()
        except Exception as error:  # noqa: BLE001 - apps are nonessential
            logger.warning("workspace startup failed: %s", error)
            result = {"error": str(error)}
        self.report.workspace = result
        self._mark("workspace_ready", started)
        return result
