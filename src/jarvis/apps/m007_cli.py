"""CLI commands for M007: daemon, activation control, claps, workspace, autostart, tools."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO

COMMANDS = ("daemon", "activate", "sleep", "status", "quit", "claps", "workspace", "autostart", "tools", "welcome")


def run(command: str, args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    handler = globals()[f"_{command}"]
    return handler(args, config, out, err)


def _emit(out: TextIO, data: Any) -> None:
    out.write(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n")
    out.flush()


def _daemon(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    from jarvis.apps.daemon import configure_file_logging, run_daemon  # noqa: PLC0415

    log = configure_file_logging()
    err.write(f"jarvis sentinel running; log: {log}\n")
    try:
        return asyncio.run(run_daemon(config))
    except KeyboardInterrupt:
        return 130


def _control(command: str, config: Any, out: TextIO, err: TextIO) -> int:
    from jarvis.apps.daemon import DEFAULT_PORT, send_command  # noqa: PLC0415

    try:
        _emit(out, send_command(command, int(config.daemon.get("control_port", DEFAULT_PORT))))
    except OSError:
        err.write("error: the Jarvis daemon is not running (start it with: jarvis daemon)\n")
        return 1
    return 0


def _activate(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("activate", config, out, err)


def _sleep(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("sleep", config, out, err)


def _status(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("status", config, out, err)


def _quit(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("quit", config, out, err)


def _claps(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    action = (args.args or ["test"])[0]
    if action == "test":
        return _claps_test(config, args.seconds or 30.0, out)
    if action == "calibrate":
        return _claps_calibrate(config, out)
    err.write("usage: jarvis claps test|calibrate [--seconds N]\n")
    return 2


def _record(config: Any, seconds: float, rate: int) -> Any:
    import sounddevice as sd  # noqa: PLC0415

    device = config.daemon.get("input_device", config.audio.input_device)
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=device)
    sd.wait()
    return audio[:, 0]


def _claps_test(config: Any, seconds: float, out: TextIO) -> int:
    """Live detector with the configured tuning; prints each clap and gesture."""
    import sounddevice as sd  # noqa: PLC0415

    from jarvis.adapters.audio.claps import ClapDetector  # noqa: PLC0415
    from jarvis.apps.daemon import clap_tuning  # noqa: PLC0415

    detector = ClapDetector(clap_tuning(config))
    events: list[dict[str, Any]] = []
    seen = 0
    cpu = [0.0]

    def _cb(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        nonlocal seen
        started = time.perf_counter()
        gesture = detector.feed(indata[:, 0])
        cpu[0] += time.perf_counter() - started
        for clap in detector.accepted[seen:]:
            events.append({"clap": clap.time, "confidence": clap.confidence, **clap.features})
        seen = len(detector.accepted)
        if gesture is not None:
            events.append({"GESTURE": gesture.time, "confidence": gesture.confidence, "latency_ms": round(gesture.latency_seconds * 1000)})

    out.write(f"Escuchando {seconds:.0f} s: da {detector.tuning.claps_required} palmadas...\n")
    out.flush()
    device = config.daemon.get("input_device", config.audio.input_device)
    with sd.InputStream(samplerate=detector.tuning.sample_rate, channels=1, dtype="float32", blocksize=320, device=device, callback=_cb):
        deadline = time.monotonic() + seconds
        printed = 0
        while time.monotonic() < deadline:
            time.sleep(0.1)
            for event in events[printed:]:
                out.write(json.dumps(event) + "\n")
            printed = len(events)
            out.flush()
    _emit(out, {"gestures": sum("GESTURE" in e for e in events), "claps": sum("clap" in e for e in events),
                "rejected_transients": len(detector.rejected), "detector_cpu_percent_of_one_core": round(100 * cpu[0] / seconds, 3),
                "tuning": {"sensitivity": detector.tuning.sensitivity, "min_peak_dbfs": detector.tuning.min_peak_dbfs}})
    return 0


def _claps_calibrate(config: Any, out: TextIO) -> int:
    from jarvis.adapters.audio.claps import ClapTuning, calibrate, calibration_path  # noqa: PLC0415
    from jarvis.application.runtime import _data_dir  # noqa: PLC0415

    tuning = ClapTuning(**{k: v for k, v in config.claps.items() if k != "enabled"})
    out.write("1/2 Silencio durante 4 segundos (ruido de la habitación)...\n")
    out.flush()
    noise = _record(config, 4.0, tuning.sample_rate)
    out.write("2/2 Ahora da dos palmadas, espera un segundo, y otras dos (8 segundos)...\n")
    out.flush()
    claps = _record(config, 8.0, tuning.sample_rate)
    result = calibrate(noise, claps, tuning)
    if result.get("ok"):
        path = calibration_path(_data_dir())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=1), encoding="utf-8")
        result["saved_to"] = str(path)
    _emit(out, result)
    return 0 if result.get("ok") else 1


def _workspace(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    from jarvis.application.runtime import build_workspace  # noqa: PLC0415

    manager = build_workspace(config)
    if manager is None:
        err.write("error: no workspace.profiles configured\n")
        return 2
    action = (args.args or ["status"])[0]
    profile = (args.args[1:2] or [args.profile or config.workspace.get("default_profile") or next(iter(manager.profiles))])[0]
    if action == "start":
        started = time.perf_counter()
        results = asyncio.run(manager.start(profile))
        _emit(out, {"profile": profile, "total_ms": round((time.perf_counter() - started) * 1000), "tasks": [r.__dict__ for r in results]})
        return 0 if all(r.ok for r in results) else 1
    if action == "stop":
        results = asyncio.run(manager.stop(profile, force=bool(args.force)))
        _emit(out, {"profile": profile, "tasks": [r.__dict__ for r in results]})
        return 0
    if action == "status":
        _emit(out, {name: {"tasks": [t.name for t in p.tasks], "managed": manager.managed(name)} for name, p in manager.profiles.items()})
        return 0
    err.write("usage: jarvis workspace start|stop|status [profile] [--force]\n")
    return 2


def _autostart(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    from jarvis.apps import autostart  # noqa: PLC0415

    action = (args.args or ["status"])[0]
    try:
        if action == "install":
            _emit(out, {"installed": str(autostart.install(Path(args.config)))})
        elif action == "remove":
            _emit(out, {"removed": autostart.remove()})
        elif action == "status":
            _emit(out, autostart.status())
        else:
            err.write("usage: jarvis autostart install|remove|status\n")
            return 2
    except RuntimeError as error:
        err.write(f"error: {error}\n")
        return 1
    return 0


def _welcome(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    """Record the cloned-voice startup welcomes (after enrolling the voice or editing the text)."""
    if (args.args or ["record"])[0] != "record":
        err.write("usage: jarvis welcome record\n")
        return 2
    from jarvis.adapters.tts.ack_cache import join_wavs  # noqa: PLC0415
    from jarvis.adapters.tts.resolve import build_qwen_clone  # noqa: PLC0415
    from jarvis.apps.daemon import Sentinel, startup_options  # noqa: PLC0415
    from jarvis.application.startup import welcome_texts  # noqa: PLC0415
    from jarvis.core.turn import TurnContext  # noqa: PLC0415

    cache = Sentinel.welcome_cache_for(config)
    clone = build_qwen_clone(config)

    async def _record() -> list[str]:
        await clone.start()
        try:
            info = await asyncio.wait_for(clone.wait_ready(), 180)
            if not info.get("profile"):
                raise RuntimeError("no voice profile enrolled")
            done = []
            for text in welcome_texts(startup_options(config)):
                async def _one(value: str = text) -> Any:
                    yield value

                chunks = [c async for c in clone.synthesize(_one(), TurnContext.fresh("welcome-cache"))]
                cache.put(text, join_wavs(chunks))
                done.append(text)
            return done
        finally:
            await clone.stop()

    try:
        recorded = asyncio.run(_record())
    except Exception as error:  # noqa: BLE001 - reported to the owner
        err.write(f"error: {error}\n")
        return 1
    _emit(out, {"recorded": recorded})
    return 0


def _tools(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    from jarvis.application.runtime import build_runtime  # noqa: PLC0415

    runtime = build_runtime(config)
    rows = [{"name": t.name, "risk": t.risk.value, "description": getattr(t, "description", "")} for t in runtime.components.tools.tools()]
    _emit(out, {"scopes": [str(s) for s in runtime.components.tools.scopes], "tools": rows})
    return 0
