"""Background sentinel: sleep cheaply, wake Jarvis on a gesture, go back to sleep.

Activation state machine (``Sentinel.state``)::

    sentinel ──1st clap──► candidate ──2nd clap / hotkey / wake word──► starting ──► active
       ▲                      │ no 2nd clap in time (speculative voice load undone)   │
       └──────────────────────┘                     "a dormir" / idle timeout / sleep ◄┘

The first clap only starts a speculative load of the shared voice model; the
second confirms and triggers the chime, music, workspace and welcome. Claps
during an activation are ignored (the mic is closed and the detector is in
cooldown); activations queued meanwhile are dropped. The voice model outlives
sessions (see :mod:`jarvis.application.voice_manager`).

Only one daemon runs per user: the control socket bind on 127.0.0.1 doubles as
the single-instance lock, and ``jarvis activate`` / ``jarvis sleep`` talk to
it. Runs headless under ``pythonw.exe`` (see :mod:`jarvis.apps.autostart`),
logging to ``%LOCALAPPDATA%\\jarvis\\logs\\daemon.log``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from jarvis.adapters.audio.claps import ClapDetector, ClapTuning, calibration_path, load_calibration
from jarvis.application.runtime import JarvisRuntime, _data_dir, build_runtime
from jarvis.application.voice_manager import VoiceModelManager, VoicePolicy
from jarvis.observability.event_hub import JsonlSink, hub
from jarvis.adapters.tts.ack_cache import bytes_to_stream, join_wavs
from jarvis.application.startup import StartupOptions, StartupSequence, welcome_texts
from jarvis.core.turn import TurnContext
from jarvis.config import RuntimeConfig

logger = logging.getLogger("jarvis.daemon")

DEFAULT_PORT = 47811
_BLOCK = 320  # 20 ms at 16 kHz


def clap_tuning(config: RuntimeConfig) -> ClapTuning:
    """Clap detector settings: config values over the saved calibration."""
    values = {k: v for k, v in config.claps.items() if k != "enabled"}
    tuning = ClapTuning(**values)
    # A saved calibration refines thresholds unless the owner pinned them in config.
    calibrated = load_calibration(tuning, calibration_path(_data_dir()))
    return replace(calibrated, **{k: values[k] for k in ("min_peak_dbfs", "sensitivity") if k in values})


def startup_options(config: RuntimeConfig) -> StartupOptions:
    """Welcome sequence options from the ``welcome`` config section."""
    values = dict(config.welcome)
    if "essential" in values:
        values["essential"] = tuple(values["essential"])
    return StartupOptions(**values)


# -- global hotkey ------------------------------------------------------------------

_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+j' -> (modifiers, virtual-key code)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    modifiers = 0
    for mod in mods:
        if mod not in _MODS:
            raise ValueError(f"unknown modifier {mod!r}")
        modifiers |= _MODS[mod]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"unsupported key {key!r}")
    return modifiers | 0x4000, vk  # MOD_NOREPEAT


def start_hotkey_thread(spec: str, on_press: Callable[[], None]) -> Optional[threading.Thread]:
    """RegisterHotKey on a dedicated message-loop thread (documented Win32 API)."""
    if sys.platform != "win32" or not spec:
        return None
    modifiers, vk = parse_hotkey(spec)

    def _loop() -> None:
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, modifiers, vk):
            logger.warning("hotkey %s is taken by another application", spec)
            return
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                on_press()

    import ctypes.wintypes  # noqa: F401,PLC0415 - registers ctypes.wintypes

    thread = threading.Thread(target=_loop, name="jarvis-hotkey", daemon=True)
    thread.start()
    return thread


# -- sentinel ---------------------------------------------------------------------

class Sentinel:
    """Background daemon that waits for an activation and runs one voice session at a time.

    Listens for claps, the hotkey, the optional wake word and control-socket
    commands; on activation it plays the startup sequence, runs the voice loop
    until the owner sends it to sleep, then returns to listening.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        runtime_factory: Callable[[RuntimeConfig], JarvisRuntime] = build_runtime,
        mixer_factory: Optional[Callable[[], Any]] = None,
        open_mic: Optional[Callable[[Callable[[Any], None]], Any]] = None,
    ) -> None:
        self.config = config
        self._runtime_factory = runtime_factory
        self._mixer_factory = mixer_factory or self._default_mixer
        self._open_mic = open_mic or self._default_mic
        self.detector = ClapDetector(clap_tuning(config))
        self.detector.on_candidate = lambda clap: self._threadsafe(self._on_candidate, clap)
        self.detector.on_candidate_expired = lambda reason: self._threadsafe(self._on_candidate_expired, reason)
        self.voice = VoiceModelManager(self._clone_factory, VoicePolicy.from_config(config.voice))
        self._tasks: set[asyncio.Task[Any]] = set()
        self._after_session = ""  # "restart" | "shutdown": requested by voice
        self._voice_gate: Optional[asyncio.Event] = None
        self.restart_requested = False
        self._claps_enabled = bool(config.claps.get("enabled", True))
        self._wake = self._make_wake_detector()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._activation: Optional[asyncio.Queue[str]] = None
        self.state = "idle"
        self.runtime: Optional[JarvisRuntime] = None
        self.sequence: Optional[StartupSequence] = None
        self.last_report: dict[str, Any] = {}
        self._stop = asyncio.Event()
        self._last_gesture: dict[str, Any] = {}
        self._mixer: Any = None
        self._prepared: Optional[asyncio.Task[JarvisRuntime]] = None
        self.welcome_cache = self.welcome_cache_for(config)

    @staticmethod
    def welcome_cache_for(config: RuntimeConfig) -> Any:
        """Versioned cache shared with the fast-command acks (re-enrolling invalidates it)."""
        from jarvis.adapters.tts.voice_cache import VoiceCache, voice_identity  # noqa: PLC0415

        return VoiceCache(_data_dir() / "cache" / "voice", voice_identity(config))

    def _clone_factory(self) -> Any:
        from jarvis.adapters.tts.resolve import build_qwen_clone, tts_provider  # noqa: PLC0415

        return build_qwen_clone(self.config) if tts_provider(self.config) == "qwen_clone" else None

    # -- activation sources (thread-safe) --------------------------------------
    def _threadsafe(self, callback: Callable[..., Any], *args: Any) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(callback, *args)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def request_activation(self, source: str) -> None:
        if self._loop is None or self._activation is None:
            return
        self._loop.call_soon_threadsafe(self._activation.put_nowait, (source, time.perf_counter()))

    def _on_candidate(self, clap: Any) -> None:
        """First clap: speculative warm-up only (no chime, no music, no startup)."""
        if self.state != "sentinel":
            return
        self.state = "candidate"
        hub.publish("activation.started", source="first_clap")
        self._spawn(self.voice.speculate("first clap"))
        self._spawn(self._prime_output())

    async def _prime_output(self) -> None:
        """Open the speaker stream on the first clap: the chime is instant on the second."""
        try:
            await asyncio.to_thread(self._get_mixer().prime)
        except Exception as error:  # noqa: BLE001 - activation still works, just slower
            logger.debug("output not primed: %s", error)

    def _on_candidate_expired(self, reason: str) -> None:
        if self.state != "candidate":
            return
        self.state = "sentinel"
        last = self.detector.rejected[-1] if self.detector.rejected else None
        recent = last if last and self.detector.now - last["time"] < 2.0 else None
        logger.info("activation candidate cancelled: %s%s", reason, f" (last rejected sound: {recent})" if recent else "")
        hub.publish("activation.cancelled", reason=reason)
        self._spawn(self.voice.cancel_speculation(reason))

    def _on_audio(self, block: Any) -> None:
        """PortAudio callback thread: cheap feature extraction only."""
        mono = block[:, 0] if getattr(block, "ndim", 1) > 1 else block
        if self._claps_enabled:
            gesture = self.detector.feed(mono)
            if gesture is not None:
                self._last_gesture = {"confidence": gesture.confidence, "latency_s": round(gesture.latency_seconds, 3), "at": time.time()}
                logger.info("%d claps: confidence %.2f, confirmed %.0f ms after the last clap", len(gesture.claps), gesture.confidence, gesture.latency_seconds * 1000)
                self.request_activation("claps")
        if self._wake is not None:
            self._wake_buffer.append(mono.copy())
            if sum(len(b) for b in self._wake_buffer) >= 1280:
                import numpy as np  # noqa: PLC0415

                frame = np.concatenate(self._wake_buffer)
                self._wake_buffer.clear()
                pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
                try:
                    if self._wake.detected(pcm):
                        if self.voice.policy.predictive_on_wake_word:
                            self._threadsafe(lambda: self._spawn(self.voice.speculate("wake word")))
                        self.request_activation("wake_word")
                except Exception as error:  # noqa: BLE001 - disable a broken detector, keep claps
                    logger.warning("wake word disabled: %s", error)
                    self._wake = None

    def _make_wake_detector(self) -> Any:
        if not self.config.daemon.get("wake_word", False):
            return None
        from jarvis.adapters.audio.wake import OpenWakeWordDetector, openwakeword_available  # noqa: PLC0415

        if not openwakeword_available():
            logger.warning("openwakeword not installed; voice activation disabled")
            return None
        self._wake_buffer: list[Any] = []
        return OpenWakeWordDetector(model_name=str(self.config.daemon.get("wake_word_model", "hey_jarvis")), threshold=0.5)

    # -- device factories ---------------------------------------------------------
    def _default_mixer(self) -> Any:
        from jarvis.adapters.audio.mixer import Mixer  # noqa: PLC0415

        return Mixer(device=self.config.audio.output_device)

    def _default_mic(self, callback: Callable[[Any], None]) -> Any:
        import sounddevice as sd  # noqa: PLC0415

        def _cb(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            try:
                callback(indata)
            except Exception:  # noqa: BLE001 - never kill the audio thread
                logger.exception("clap listener callback failed")

        device = self.config.daemon.get("input_device", self.config.audio.input_device)
        stream = sd.InputStream(samplerate=self.detector.tuning.sample_rate, channels=1, dtype="float32", blocksize=_BLOCK, device=device, callback=_cb)
        stream.start()
        return stream

    # -- main loop ------------------------------------------------------------------
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._activation = asyncio.Queue()
        start_hotkey_thread(str(self.config.daemon.get("hotkey", "ctrl+alt+j")), lambda: self.request_activation("hotkey"))
        preload = asyncio.create_task(self._preload_music())
        self._prepared = asyncio.create_task(self._prepare_runtime())
        if self.config.daemon.get("events_log", True):
            unsubscribe = hub.subscribe(JsonlSink(_data_dir() / "logs" / "events.jsonl"))
        else:
            unsubscribe = lambda: None  # noqa: E731
        self._spawn(self.voice.start_always())
        self._spawn(self._maintenance())
        try:
            while not self._stop.is_set():
                activation = await self._listen()
                if activation is None:
                    break
                await self._session(*activation)
                if self._after_session:
                    self.restart_requested = self._after_session == "restart"
                    logger.info("%s requested by voice", self._after_session)
                    self._stop.set()
                    break
                self._prepared = asyncio.create_task(self._prepare_runtime())
        finally:
            preload.cancel()
            for task in list(self._tasks):
                task.cancel()
            if self._prepared is not None:
                self._prepared.cancel()
                runtime = await asyncio.gather(self._prepared, return_exceptions=True)
                if isinstance(runtime[0], JarvisRuntime):
                    await runtime[0].stop()
            await self.voice.shutdown()
            unsubscribe()

    def _build_runtime(self) -> JarvisRuntime:
        shared = self.voice.tts
        return self._runtime_factory(self.config, voice_clone=shared) if shared is not None else self._runtime_factory(self.config)

    async def _prepare_runtime(self) -> JarvisRuntime:
        """Build the next session's runtime while idle (the build takes seconds)."""
        return await asyncio.to_thread(self._build_runtime)

    async def _maintenance(self) -> None:
        """Every 30 s: GPU-pressure eviction and optional host metrics."""
        metrics_every = float(self.config.daemon.get("metrics_interval_seconds", 0))
        last_metrics = 0.0
        while True:
            await asyncio.sleep(min(30.0, metrics_every) if metrics_every > 0 else 30.0)
            try:
                await self.voice.check_pressure()
                if metrics_every > 0 and time.monotonic() - last_metrics >= metrics_every:
                    last_metrics = time.monotonic()
                    hub.publish("system.metrics", **await asyncio.to_thread(_metrics))
            except Exception:  # noqa: BLE001 - maintenance never kills the sentinel
                logger.debug("maintenance failed", exc_info=True)

    def _get_mixer(self) -> Any:
        if self._mixer is None:
            self._mixer = self._mixer_factory()
        return self._mixer

    async def _preload_music(self) -> None:
        """Decode the startup track while idle so activation plays it at once."""
        path = startup_options(self.config).music_path
        if not path:
            return
        try:
            await asyncio.to_thread(self._get_mixer().load, path)
            logger.info("startup music preloaded: %s", path)
        except Exception as error:  # noqa: BLE001 - reported again at activation
            logger.warning("startup music not preloaded: %s", error)

    async def _listen(self) -> Optional[tuple[str, float]]:
        self.state = "sentinel"
        while not self._activation.empty():  # presses made while Jarvis was awake
            self._activation.get_nowait()
        stream = None
        try:
            stream = self._open_mic(self._on_audio)
        except Exception as error:  # noqa: BLE001 - hotkey/socket still work without a mic
            logger.warning("clap listener unavailable: %s", error)
        self.detector.resume(cooldown=True)
        logger.info("sentinel listening (claps=%s, wake_word=%s)", self._claps_enabled, self._wake is not None)
        stop = asyncio.ensure_future(self._stop.wait())
        get = asyncio.ensure_future(self._activation.get())
        try:
            done, _ = await asyncio.wait({stop, get}, return_when=asyncio.FIRST_COMPLETED)
            return get.result() if get in done else None
        finally:
            for fut in (stop, get):
                if not fut.done():
                    fut.cancel()
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            self.detector.suspend()  # our own music and voice must never re-trigger

    async def _session(self, source: str, gesture_at: float) -> None:
        self.state = "starting"
        hub.publish("activation.confirmed", source=source, confidence=self._last_gesture.get("confidence") if source == "claps" else None)
        # Pin the shared voice for the whole session (loads it if still cold). Started
        # once the music plays: spawning the worker must never delay chime or music.
        self._voice_gate = asyncio.Event()
        acquire = asyncio.create_task(self._acquire_voice_after(self._voice_gate))
        mixer = None
        try:
            mixer = self._get_mixer()
        except Exception as error:  # noqa: BLE001 - no output device: continue silently
            logger.warning("mixer unavailable: %s", error)
        holder: dict[str, JarvisRuntime] = {}

        async def start_services() -> list[Any]:
            # Built off the loop (~3 s of imports/model setup) so the chime and the
            # music are not held back; the runtime owns no thread-bound resources.
            prepared, self._prepared = self._prepared, None
            runtime = await prepared if prepared is not None else await asyncio.to_thread(self._build_runtime)
            holder["runtime"] = self.runtime = runtime
            await runtime.start()
            return list(runtime.supervisor.health_snapshot())

        async def speak(text: str) -> None:
            await holder["runtime"].components.turn_manager.speak_text(text)

        async def wait_voice() -> bool:
            return await _wait_voice(holder["runtime"])

        async def speak_fallback(text: str) -> None:
            # Truthful warning without waiting for the clone: the chain's last provider (SAPI).
            runtime = holder["runtime"]
            tts = runtime.components.turn_manager._tts
            fallback = getattr(tts, "providers", (tts,))[-1]

            async def _one() -> Any:
                yield text

            await runtime.components.audio_output.play(fallback.synthesize(_one(), TurnContext.fresh("warning")), TurnContext.fresh("warning"))

        sequence = self.sequence = StartupSequence(
            startup_options(self.config),
            mixer=mixer,
            speak=speak,
            start_services=start_services,
            wait_voice=wait_voice,
            start_workspace=self._workspace_starter(),
            on_phase=self._on_startup_phase,
            speak_fallback=speak_fallback,
            cached_welcome=self.welcome_cache.get,
            play_audio=lambda audio: holder["runtime"].components.audio_output.play(bytes_to_stream(audio), TurnContext.fresh("welcome")),
        )
        offset_ms = (time.perf_counter() - gesture_at) * 1000
        try:
            report = await sequence.trigger(source)
            runtime = holder.get("runtime")
            timings = {k: round(v + offset_ms, 1) for k, v in report.timings_ms.items()}
            self.last_report = {**report.as_dict(), "timings_ms_since_gesture": timings, "gesture": self._last_gesture if source == "claps" else None}
            self._write_report()
            if not report.issues and report.phase.value == "ready":
                hub.publish("startup.completed", welcome_source=report.welcome_source, timings_ms=timings)
            if runtime is None:
                return
            self.state = "active"
            self._register_session_tools(runtime, mixer)
            # The owner may answer the welcome ("para la música") without the wake word.
            if hasattr(runtime, "activation"):
                runtime.activation.note_turn_complete()
            recorder = asyncio.create_task(self._record_welcomes(runtime, sequence.options))
            watchdog = asyncio.create_task(self._idle_watchdog(runtime))
            ducker = asyncio.create_task(_duck_during_speech(mixer, runtime, sequence.options)) if mixer and mixer.music_playing else None
            await runtime.run_until_stopped()
            watchdog.cancel()
            recorder.cancel()
            if ducker is not None:
                ducker.cancel()
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                await sequence.cancel()
                raise  # the daemon itself is shutting down
            logger.info("startup interrupted by the owner")  # sleep() cancelled the sequence
        except Exception:  # noqa: BLE001 - a failed session must not kill the sentinel
            logger.exception("voice session failed")
        finally:
            if sequence.workspace_task is not None:
                await asyncio.gather(sequence.workspace_task, return_exceptions=True)
                self.last_report["workspace"] = sequence.report.workspace
                self.last_report["timings_ms_since_gesture"] = {k: round(v + offset_ms, 1) for k, v in sequence.report.timings_ms.items()}
                self._write_report()
            runtime = holder.get("runtime")
            if runtime is not None:
                await runtime.stop()
            if mixer is not None:
                mixer.stop()
            await asyncio.gather(acquire, return_exceptions=True)
            await self.voice.release("session")  # warm -> cooldown (reused by the next session)
            if runtime is not None:
                _release_models(runtime)
            self.runtime = None
            self.sequence = None  # its closures hold the finished runtime
            logger.info("voice session ended; back to sentinel")

    def _register_session_tools(self, runtime: JarvisRuntime, mixer: Any) -> None:
        """Voice control of this session's music and of the daemon itself."""
        from jarvis.adapters.tools.desktop import AssistantControlTool, MusicTool  # noqa: PLC0415

        tools = getattr(runtime.components, "tools", None)
        if tools is None:
            return
        tools.register(MusicTool(mixer, media_key=lambda: tools.execute("media_play_pause", {}, TurnContext.fresh("music"))))
        tools.register(AssistantControlTool("restart", lambda: self._end_session("restart")))
        tools.register(AssistantControlTool("shutdown", lambda: self._end_session("shutdown")))

    def _end_session(self, then: str) -> None:
        self._after_session = then
        if self.runtime is not None:
            self.runtime.voice_loop.request_stop_after_turn()

    def request_restart(self) -> None:
        """Control socket / CLI: restart now (ends a session, or the idle sentinel)."""
        self._after_session = "restart"
        if self.runtime is not None and self.state == "active":
            self.runtime.voice_loop.request_stop()
        else:
            self.restart_requested = True
            self._stop.set()

    async def _acquire_voice_after(self, gate: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(gate.wait(), 5.0)
        except asyncio.TimeoutError:
            pass
        await self.voice.acquire("session")

    def _on_startup_phase(self, phase: Any, detail: dict[str, Any]) -> None:
        hub.publish("startup.progress", phase=phase.value)
        if phase.value != "acknowledged" and self._voice_gate is not None:
            self._voice_gate.set()  # chime and music are out: now load the voice
        issues = detail.get("issues")
        if issues and phase.value in ("announcing", "failed"):
            # Visible even if no voice ever speaks: published and logged before the warning.
            logger.warning("startup degraded: %s", ", ".join(issues))
            hub.publish("startup.degraded", issues=list(issues), phase=phase.value)

    async def _idle_watchdog(self, runtime: JarvisRuntime) -> None:
        """End a session nobody is talking to (it otherwise transcribes noise all night)."""
        limit = float(self.config.daemon.get("session_idle_seconds", 900))
        if limit <= 0:
            return
        while True:
            await asyncio.sleep(min(30.0, limit / 4))
            last = getattr(runtime.voice_loop, "last_activity", None)
            if last is not None and time.monotonic() - last > limit:
                logger.info("no interaction for %.0f s: going back to sleep", limit)
                runtime.voice_loop.request_stop()
                return

    async def _record_welcomes(self, runtime: JarvisRuntime, options: StartupOptions) -> None:
        """Once the clone is ready, record the 'all operational' welcomes in its voice."""
        clone = _clone(runtime)
        missing = [text for text in welcome_texts(options) if self.welcome_cache.get(text) is None]
        if clone is None or not missing:
            return
        try:
            if not await _wait_voice(runtime):
                return
            for text in missing:
                async def _one(value: str = text) -> Any:
                    yield value

                chunks = [chunk async for chunk in clone.synthesize(_one(), TurnContext.fresh("welcome-cache"))]
                if chunks:
                    self.welcome_cache.put(text, join_wavs(chunks))
                    logger.info("recorded cloned-voice welcome: %s", text[:40])
        except Exception as error:  # noqa: BLE001 - retried next session
            logger.warning("welcome recording failed: %s", error)

    def _write_report(self) -> None:
        _write_json(_data_dir() / "startup-report.json", self.last_report)

    def _workspace_starter(self) -> Optional[Callable[[], Any]]:
        from jarvis.application.runtime import build_workspace  # noqa: PLC0415

        profile = self.config.workspace.get("startup_profile") or self.config.workspace.get("default_profile")
        workspace = build_workspace(self.config) if profile else None
        if workspace is None:
            return None

        async def _start() -> Any:
            results = await workspace.start(profile)
            return {r.name: {"status": r.status, "ms": r.elapsed_ms, "detail": r.detail} for r in results}

        return _start

    # -- control -------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        detector = self.detector
        listener = {
            "listened_seconds": round(detector.now, 1),
            "noise_floor_dbfs": round(20 * __import__("math").log10(max(detector._floor, 1e-9)), 1),
            "claps_accepted": [{"time": c.time, **c.features} for c in detector.accepted[-6:]],
            "transients_rejected": detector.rejected[-3:],
        }
        return {"state": self.state, "voice": self.voice.snapshot(), "voice_cache_entries": self.welcome_cache.stats()["entries"], "listener": listener, "last_gesture": self._last_gesture, "last_report": self.last_report}

    def sleep(self) -> None:
        """Stop talking: ends the session, or interrupts a startup in progress."""
        if self.state == "starting" and self.sequence is not None and self.sequence.running:
            self._spawn(self.sequence.cancel())  # music fades, no welcome
        if self.runtime is not None:
            # Also covers a startup that finishes right now: the loop exits at once.
            self.runtime.voice_loop.request_stop()

    def shutdown(self) -> None:
        self._stop.set()
        self.sleep()


def _metrics() -> dict[str, Any]:
    import psutil  # noqa: PLC0415

    from jarvis.adapters.tools.desktop import gpu_status  # noqa: PLC0415

    return {"cpu_percent": psutil.cpu_percent(0.2), "ram_percent": psutil.virtual_memory().percent, "gpu": gpu_status()}


def _release_models(runtime: JarvisRuntime) -> None:
    """Free the finished session's Whisper model (RAM + ~1 GB VRAM) right away."""
    import gc  # noqa: PLC0415

    stt = getattr(runtime.voice_loop, "_stt", None)
    for provider in getattr(stt, "providers", (stt,)):
        if hasattr(provider, "_model"):
            provider._model = None
    gc.collect()


def _clone(runtime: JarvisRuntime) -> Any:
    from jarvis.adapters.tts.qwen_clone import QwenCloneTTS  # noqa: PLC0415

    tts = getattr(runtime.components.turn_manager, "_tts", None)
    return next((p for p in getattr(tts, "providers", (tts,)) if isinstance(p, QwenCloneTTS)), None)


async def _wait_voice(runtime: JarvisRuntime) -> bool:
    clone = _clone(runtime)
    if clone is None:
        return True  # no clone configured: the configured voice is the voice
    info = await clone.wait_ready()
    return bool(info.get("profile"))


# Mic RMS (int16) per unit of music gain through the laptop speakers, measured
# p95 on the target laptop (music at gain 0.8 -> -20.7 dBFS at the mic).
MUSIC_MIC_COUPLING = 3800.0


async def _duck_during_speech(mixer: Any, runtime: JarvisRuntime, options: StartupOptions) -> None:
    """While the music plays: duck it under Jarvis's voice and keep the VAD above it.

    ponytail: the energy VAD threshold is raised by the music's expected level at
    the mic (measured coupling), so the music is not mistaken for the owner still
    talking. Upgrade path: acoustic echo cancellation with the mixer as reference.
    """
    vad = getattr(runtime.voice_loop, "_vad", None)
    base = getattr(vad, "threshold", None)
    ducked = False
    try:
        while mixer.music_playing:
            speaking = runtime.components.audio_output.state().get("current_turn") is not None
            if speaking != ducked:
                mixer.ramp(options.background_volume * 0.4 if speaking else options.background_volume, options.duck_seconds)
                ducked = speaking
            if base is not None:
                vad.threshold = base + 1.5 * MUSIC_MIC_COUPLING * mixer.gain
            await asyncio.sleep(0.05)
    finally:
        if base is not None:
            vad.threshold = base


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except OSError:
        logger.warning("could not write %s", path)


# -- control socket (single instance + CLI) ------------------------------------------

async def serve(sentinel: Sentinel, port: int) -> asyncio.AbstractServer:
    """Bind the control socket; raises OSError when another daemon owns it."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            command = (await asyncio.wait_for(reader.readline(), 5)).decode("utf-8", "replace").strip()
            if command == "activate":
                sentinel.request_activation("control")
                reply: Any = {"ok": True}
            elif command == "sleep":
                sentinel.sleep()
                reply = {"ok": True}
            elif command == "status":
                reply = sentinel.status()
            elif command == "quit":
                sentinel.shutdown()
                reply = {"ok": True}
            elif command == "restart":
                sentinel.request_restart()
                reply = {"ok": True}
            else:
                reply = {"ok": False, "error": "unknown command"}
            writer.write((json.dumps(reply, default=str) + "\n").encode("utf-8"))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(_handle, "127.0.0.1", port)


def send_command(command: str, port: int = DEFAULT_PORT, timeout: float = 5.0) -> dict[str, Any]:
    """Send one command to the running daemon's control socket and return its JSON reply."""
    import socket  # noqa: PLC0415

    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
        conn.sendall((command + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode("utf-8") or "{}")


def configure_file_logging() -> Path:
    """Log to a rotating ``daemon.log`` in the data directory; return its path."""
    log_dir = _data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "daemon.log"
    from logging.handlers import RotatingFileHandler  # noqa: PLC0415

    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("jarvis")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


async def run_daemon(config: RuntimeConfig) -> int:
    """Run the sentinel and its control socket until ``quit``; refuses a second instance."""
    sentinel = Sentinel(config)
    port = int(config.daemon.get("control_port", DEFAULT_PORT))
    try:
        server = await serve(sentinel, port)
    except OSError:
        logger.info("another Jarvis daemon owns port %d; exiting", port)
        return 3
    async with server:
        await sentinel.run()
    if sentinel.restart_requested:
        # The port is free now: start a fresh daemon exactly as this one was started.
        relaunch()
    return 0


def relaunch_argv(executable: str, argv: list[str]) -> list[str]:
    """Command that starts this daemon again (pythonw launcher or `python -m jarvis daemon`)."""
    if argv and argv[0].lower().endswith(".pyw"):
        return [executable, *argv]
    return [executable, "-m", "jarvis", *argv[1:]]


def relaunch() -> None:
    """Start a detached copy of this daemon (used by the voice ``reiníciate`` command)."""
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    command = relaunch_argv(sys.executable, list(sys.argv))
    logger.info("restarting: %s", command)
    subprocess.Popen(command, cwd=os.getcwd(), creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
