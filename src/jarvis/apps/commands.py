"""Operator CLI commands: daemon, activation control, claps, workspace, autostart, tools."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, TextIO

COMMANDS = ("daemon", "activate", "sleep", "status", "quit", "restart", "claps", "workspace", "autostart", "tools", "welcome", "startup", "ui")


def run(command: str, args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    """Dispatch *command* to its ``_<command>`` handler and return the exit code."""
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


def _find_edge() -> str | None:
    """Edge on PATH, else its standard Windows install locations."""
    found = shutil.which("msedge")
    if found:
        return found
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _launch_edge_app(edge: str, url: str) -> None:
    detach: dict[str, Any] = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    subprocess.Popen([edge, f"--app={url}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detach)


def _ui(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    """Open the command center in an Edge app window (or the default browser); the token never reaches the terminal except with --print."""
    from jarvis.apps.daemon import DEFAULT_PORT, send_command  # noqa: PLC0415

    try:
        reply = send_command("ui", int(config.daemon.get("control_port", DEFAULT_PORT)))
    except OSError:  # ConnectionError is an OSError
        err.write("Jarvis daemon is not running; start it with: jarvis daemon\n")
        return 1
    if not reply.get("ok"):
        err.write(f"error: {reply.get('error', 'ui unavailable')}\n")
        return 1
    app_url = reply.get("app_url")
    if not app_url:
        err.write("error: the running daemon does not offer the UI shell (no app_url); restart it with: jarvis restart\n")
        return 1
    if getattr(args, "print_url", False):
        out.write(f"{app_url}\n")
        return 0
    edge = _find_edge()
    if edge:
        try:
            _launch_edge_app(edge, app_url)
        except OSError as error:
            err.write(f"warning: could not start Edge ({error}); using the default browser\n")
        else:
            out.write("opened in Edge app window\n")
            return 0
    webbrowser.open(app_url)
    out.write("opened in default browser\n")
    return 0


def _activate(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("activate", config, out, err)


def _sleep(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("sleep", config, out, err)


def _status(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("status", config, out, err)


def _quit(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("quit", config, out, err)


def _restart(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    return _control("restart", config, out, err)


def _claps(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    action = (args.args or ["test"])[0]
    if action == "test":
        return _claps_test(config, args.seconds or 30.0, out, err, args.file)
    if action == "calibrate":
        return _claps_calibrate(config, out)
    err.write("usage: jarvis claps test [--seconds N | --file PATH] | calibrate\n")
    return 2


def _record(config: Any, seconds: float, rate: int) -> Any:
    import sounddevice as sd  # noqa: PLC0415

    device = config.daemon.get("input_device", config.audio.input_device)
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=device)
    sd.wait()
    return audio[:, 0]


def _claps_test(config: Any, seconds: float, out: TextIO, err: TextIO, file: str | None = None) -> int:
    """Detector with the configured tuning over the microphone, or a recording with *file*; prints each clap and gesture."""
    from jarvis.adapters.audio.claps import AUDIO_READ_ERRORS, ClapDetector, load_audio_mono  # noqa: PLC0415
    from jarvis.apps.daemon import clap_tuning  # noqa: PLC0415

    if file is None:
        import sounddevice as sd  # noqa: PLC0415

    detector = ClapDetector(clap_tuning(config))
    events: list[dict[str, Any]] = []
    seen = 0
    cpu = [0.0]

    def _feed(block: Any) -> None:
        nonlocal seen
        started = time.perf_counter()
        gesture = detector.feed(block)
        cpu[0] += time.perf_counter() - started
        for clap in detector.accepted[seen:]:
            events.append({"clap": clap.time, "confidence": clap.confidence, **clap.features})
        seen = len(detector.accepted)
        if gesture is not None:
            events.append({"GESTURE": gesture.time, "confidence": gesture.confidence, "latency_ms": round(gesture.latency_seconds * 1000)})

    if file is not None:
        try:
            audio = load_audio_mono(file, detector.tuning.sample_rate)
        except AUDIO_READ_ERRORS as error:
            err.write(f"error: cannot read {file}: {error}\n")
            return 1
        seconds = audio.size / detector.tuning.sample_rate
        out.write(f"Reproduciendo {Path(file).name} ({seconds:.1f} s): {detector.tuning.claps_required} palmadas activan...\n")
        printed = 0
        for start in range(0, audio.size, 320):
            _feed(audio[start:start + 320])
            for event in events[printed:]:
                out.write(json.dumps(event) + "\n")
            printed = len(events)
    else:
        out.write(f"Escuchando {seconds:.0f} s: da {detector.tuning.claps_required} palmadas...\n")
        out.flush()
        device = config.daemon.get("input_device", config.audio.input_device)
        with sd.InputStream(samplerate=detector.tuning.sample_rate, channels=1, dtype="float32", blocksize=320, device=device, callback=lambda indata, frames, time_info, status: _feed(indata[:, 0])):
            deadline = time.monotonic() + seconds
            printed = 0
            while time.monotonic() < deadline:
                time.sleep(0.1)
                for event in events[printed:]:
                    out.write(json.dumps(event) + "\n")
                printed = len(events)
                out.flush()
    _emit(out, {"gestures": sum("GESTURE" in e for e in events), "claps": sum("clap" in e for e in events),
                "rejected_transients": len(detector.rejected), "detector_cpu_percent_of_one_core": round(100 * cpu[0] / max(seconds, 1e-9), 3),
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


def _startup(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    """Run the cinematic startup sequence once in the foreground and print its StartupReport."""
    from jarvis.adapters.tts.ack_cache import bytes_to_stream  # noqa: PLC0415
    from jarvis.apps.daemon import Sentinel, _wait_voice, startup_options  # noqa: PLC0415
    from jarvis.application.runtime import build_runtime  # noqa: PLC0415
    from jarvis.application.startup import StartupPhase, StartupSequence  # noqa: PLC0415
    from jarvis.core.turn import TurnContext  # noqa: PLC0415

    fakes = bool(getattr(args, "use_fakes", False))
    if fakes:  # never touch the owner's memory database or an audio device
        from dataclasses import replace  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        config = replace(config, memory=replace(config.memory, db_path=str(Path(tempfile.mkdtemp()) / "jarvis.db")))
    mixer = None
    if not fakes:
        try:
            from jarvis.adapters.audio.mixer import Mixer  # noqa: PLC0415

            mixer = Mixer(device=config.audio.output_device)
        except Exception as error:  # noqa: BLE001 - no output device: continue silently, as the daemon does
            err.write(f"warning: mixer unavailable: {error}\n")
    holder: dict[str, Any] = {}

    async def start_services() -> list[Any]:
        runtime = holder["runtime"] = await asyncio.to_thread(build_runtime, config, use_fakes=fakes)
        await runtime.start()
        return list(runtime.supervisor.health_snapshot())

    async def speak(text: str) -> None:
        await holder["runtime"].components.turn_manager.speak_text(text)

    async def wait_voice() -> bool:
        return await _wait_voice(holder["runtime"])

    async def play_audio(audio: bytes) -> None:
        await holder["runtime"].components.audio_output.play(bytes_to_stream(audio), TurnContext.fresh("welcome"))

    async def speak_fallback(text: str) -> None:
        # Same as the daemon: the chain's last provider (SAPI) speaks the truthful warning without waiting for the clone.
        runtime = holder["runtime"]
        tts = runtime.components.turn_manager._tts
        fallback = getattr(tts, "providers", (tts,))[-1]

        async def _one() -> Any:
            yield text

        await runtime.components.audio_output.play(fallback.synthesize(_one(), TurnContext.fresh("warning")), TurnContext.fresh("warning"))

    sequence = StartupSequence(
        startup_options(config),
        mixer=mixer,
        speak=speak,
        start_services=start_services,
        wait_voice=wait_voice,
        speak_fallback=speak_fallback,
        cached_welcome=None if fakes else Sentinel.welcome_cache_for(config).get,
        play_audio=None if fakes else play_audio,
    )

    async def _run() -> Any:
        try:
            report = await sequence.trigger("cli")
            if mixer is not None and mixer.music_playing:
                await asyncio.sleep(sequence.options.fade_out_seconds)  # let the fade be heard
            return report
        finally:
            runtime = holder.get("runtime")
            if runtime is not None:
                await runtime.stop()
            if mixer is not None:
                mixer.stop()

    try:
        report = asyncio.run(_run())
    except KeyboardInterrupt:
        return 130
    _emit(out, report.as_dict())
    return 0 if report.phase is StartupPhase.READY else 1


def _tools(args: Any, config: Any, out: TextIO, err: TextIO) -> int:
    from jarvis.application.runtime import build_runtime  # noqa: PLC0415

    runtime = build_runtime(config)
    rows = [{"name": t.name, "risk": t.risk.value, "description": getattr(t, "description", "")} for t in runtime.components.tools.tools()]
    _emit(out, {"scopes": [str(s) for s in runtime.components.tools.scopes], "tools": rows})
    return 0
