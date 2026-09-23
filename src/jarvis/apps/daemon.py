"""Background sentinel: sleep cheaply, wake Jarvis on a gesture, go back to sleep.

States::

    sentinel (mic -> clap detector [+ optional "hey Jarvis"], global hotkey,
              localhost control socket)
      -> activation: mic closed, runtime built, cinematic startup, voice loop
      -> "Jarvis, a dormir" / control "sleep": runtime stopped, sentinel again

Only one daemon runs per user: the control socket bind on 127.0.0.1 doubles as
the single-instance lock, and ``jarvis activate`` / ``jarvis sleep`` talk to
it. Runs headless under ``pythonw.exe`` (see :mod:`jarvis.apps.autostart`),
logging to ``%LOCALAPPDATA%\\jarvis\\logs\\daemon.log``.
"""

from __future__ import annotations

import asyncio
import ctypes
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
from jarvis.application.startup import StartupOptions, StartupSequence
from jarvis.config import RuntimeConfig

logger = logging.getLogger("jarvis.daemon")

DEFAULT_PORT = 47811
_BLOCK = 320  # 20 ms at 16 kHz


def clap_tuning(config: RuntimeConfig) -> ClapTuning:
    values = {k: v for k, v in config.claps.items() if k != "enabled"}
    tuning = ClapTuning(**values)
    # A saved calibration refines thresholds unless the owner pinned them in config.
    calibrated = load_calibration(tuning, calibration_path(_data_dir()))
    return replace(calibrated, **{k: values[k] for k in ("min_peak_dbfs", "sensitivity") if k in values})


def startup_options(config: RuntimeConfig) -> StartupOptions:
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

    # -- activation sources (thread-safe) --------------------------------------
    def request_activation(self, source: str) -> None:
        if self._loop is None or self._activation is None:
            return
        self._loop.call_soon_threadsafe(self._activation.put_nowait, (source, time.perf_counter()))

    def _on_audio(self, block: Any) -> None:
        """PortAudio callback thread: cheap feature extraction only."""
        mono = block[:, 0] if getattr(block, "ndim", 1) > 1 else block
        if self._claps_enabled:
            gesture = self.detector.feed(mono)
            if gesture is not None:
                self._last_gesture = {"confidence": gesture.confidence, "latency_s": round(gesture.latency_seconds, 3), "at": time.time()}
                logger.info("triple clap: confidence %.2f, detected %.0f ms after the last clap", gesture.confidence, gesture.latency_seconds * 1000)
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
        while not self._stop.is_set():
            activation = await self._listen()
            if activation is None:
                break
            await self._session(*activation)
        preload.cancel()

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
        mixer = None
        try:
            mixer = self._get_mixer()
        except Exception as error:  # noqa: BLE001 - no output device: continue silently
            logger.warning("mixer unavailable: %s", error)
        holder: dict[str, JarvisRuntime] = {}

        async def start_services() -> list[Any]:
            # Built off the loop (~3 s of imports/model setup) so the chime and the
            # music are not held back; the runtime owns no thread-bound resources.
            runtime = await asyncio.to_thread(self._runtime_factory, self.config)
            holder["runtime"] = self.runtime = runtime
            await runtime.start()
            return list(runtime.supervisor.health_snapshot())

        async def speak(text: str) -> None:
            await holder["runtime"].components.turn_manager.speak_text(text)

        async def wait_voice() -> bool:
            return await _wait_voice(holder["runtime"])

        sequence = self.sequence = StartupSequence(
            startup_options(self.config),
            mixer=mixer,
            speak=speak,
            start_services=start_services,
            wait_voice=wait_voice,
            start_workspace=self._workspace_starter(),
            on_phase=lambda phase, detail: logger.info("startup %s %s", phase.value, detail),
        )
        offset_ms = (time.perf_counter() - gesture_at) * 1000
        try:
            report = await sequence.trigger(source)
            runtime = holder.get("runtime")
            timings = {k: round(v + offset_ms, 1) for k, v in report.timings_ms.items()}
            self.last_report = {**report.as_dict(), "timings_ms_since_gesture": timings, "gesture": self._last_gesture if source == "claps" else None}
            self._write_report()
            if runtime is None:
                return
            self.state = "active"
            ducker = asyncio.create_task(_duck_during_speech(mixer, runtime, sequence.options)) if mixer and sequence.options.after_welcome == "restore" else None
            await runtime.run_until_stopped()
            if ducker is not None:
                ducker.cancel()
        except asyncio.CancelledError:
            await sequence.cancel()
            raise
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
            self.runtime = None
            logger.info("voice session ended; back to sentinel")

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
        return {"state": self.state, "listener": listener, "last_gesture": self._last_gesture, "last_report": self.last_report}

    def sleep(self) -> None:
        if self.runtime is not None:
            self.runtime.voice_loop.request_stop()

    def shutdown(self) -> None:
        self._stop.set()
        self.sleep()


async def _wait_voice(runtime: JarvisRuntime) -> bool:
    from jarvis.adapters.tts.qwen_clone import QwenCloneTTS  # noqa: PLC0415

    tts = getattr(runtime.components.turn_manager, "_tts", None)
    clone = next((p for p in getattr(tts, "providers", (tts,)) if isinstance(p, QwenCloneTTS)), None)
    if clone is None:
        return True  # no clone configured: the configured voice is the voice
    info = await clone.wait_ready()
    return bool(info.get("profile"))


async def _duck_during_speech(mixer: Any, runtime: JarvisRuntime, options: StartupOptions) -> None:
    """Keep restored music under Jarvis's voice for the rest of the session."""
    ducked = False
    while mixer.music_playing:
        speaking = runtime.components.audio_output.state().get("current_turn") is not None
        if speaking != ducked:
            mixer.ramp(options.duck_volume if speaking else options.music_volume, options.duck_seconds)
            ducked = speaking
        await asyncio.sleep(0.05)


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
    sentinel = Sentinel(config)
    port = int(config.daemon.get("control_port", DEFAULT_PORT))
    try:
        server = await serve(sentinel, port)
    except OSError:
        logger.info("another Jarvis daemon owns port %d; exiting", port)
        return 3
    async with server:
        await sentinel.run()
    return 0
