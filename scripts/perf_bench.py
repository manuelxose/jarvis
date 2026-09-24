"""Silent, repeatable Jarvis performance benchmark (p50/p95).

    python scripts/perf_bench.py [--config config.win.json] [--only claps,route,...] [--out bench.json]

Sections: claps, route, command, startup, interrupt, stt, tts, llm, e2e, resources.
Everything that touches the audio device plays zero-amplitude buffers (or music
at gain 0), so device timings are real and nothing is audible. The GPU sections
start the real voice-clone worker and Whisper; the llm/e2e sections call the
configured cloud provider (a few short prompts).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import platform
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

import numpy as np  # noqa: E402

from jarvis.config import load_config  # noqa: E402
from jarvis.core.turn import TurnContext  # noqa: E402

RESULTS: dict[str, Any] = {}


def stats(values: list[float]) -> dict[str, Any]:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {"n": len(values), "p50": round(statistics.median(ordered), 2), "p95": round(p95, 2), "min": round(ordered[0], 2), "max": round(ordered[-1], 2)}


def silent_wav(seconds: float, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


async def one(text: str) -> AsyncIterator[str]:
    yield text


# -- claps -----------------------------------------------------------------------

def bench_claps(config: Any, trials: int = 40) -> None:
    from jarvis.adapters.audio.claps import ClapDetector, ClapTuning

    rng = np.random.default_rng(11)
    rate = 16000
    tuning_kwargs = {k: v for k, v in config.claps.items() if k != "enabled"}
    required = int(tuning_kwargs.get("claps_required", 2)) if "claps_required" in ClapTuning.__dataclass_fields__ else 3
    confirm, first = [], []
    for _ in range(trials):
        gap = float(rng.uniform(0.25, 0.55))
        onsets = [1.0 + i * gap for i in range(required)]
        signal = (rng.standard_normal(int(rate * (onsets[-1] + 2.0))) * 0.001).astype(np.float32)
        for at in onsets:
            t = np.arange(int(rate * 0.12)) / rate
            burst = np.diff(rng.standard_normal(t.size) * np.exp(-t / 0.012), prepend=0.0)
            burst = (float(rng.uniform(0.3, 0.8)) * burst / np.abs(burst).max()).astype(np.float32)
            i = int(at * rate)
            signal[i:i + burst.size] += burst
        candidate: list[float] = []
        detector = ClapDetector(ClapTuning(**{k: v for k, v in tuning_kwargs.items() if k in ClapTuning.__dataclass_fields__}))
        if hasattr(detector, "on_candidate"):
            detector.on_candidate = lambda clap, d=detector: candidate.append(d.now)
        for s in range(0, signal.size, 320):
            gesture = detector.feed(signal[s:s + 320])
            if gesture is not None:
                confirm.append((detector.now - onsets[-1]) * 1000)
                break
        if candidate:
            first.append((candidate[0] - onsets[0]) * 1000)
    RESULTS["claps"] = {
        "claps_required": required,
        "first_clap_candidate_ms": stats(first),
        "confirmation_after_last_clap_ms": stats(confirm),
        "detected": f"{len(confirm)}/{trials}",
    }


# -- routing and local commands ------------------------------------------------------

UTTERANCES = [
    "qué hora es", "sube el volumen", "pon el volumen al 40", "abre spotify", "minimiza chrome",
    "arranca mi entorno de desarrollo", "qué está usando la memoria de la GPU", "cómo va el sistema",
    "abre el proyecto en el que estaba trabajando", "explícame qué es un agujero negro",
    "mira este error y arréglalo", "cancela la operación", "muéstrame la actividad de red",
]


def bench_route(config: Any) -> None:
    from jarvis.application.routing import Router

    router = Router()
    context = TurnContext.fresh("bench")

    async def run() -> tuple[list[float], dict[str, str]]:
        times, routes = [], {}
        for _ in range(100):
            for text in UTTERANCES:
                started = time.perf_counter()
                decision = await router.route(text, context)
                times.append((time.perf_counter() - started) * 1000)
                routes[text] = decision.route + (":" + decision.command.name if decision.command else "")
        return times, routes

    times, routes = asyncio.run(run())
    RESULTS["route"] = {"intent_ms": stats(times), "routes": routes}


def bench_command(config: Any) -> None:
    """Fast command to first audio chunk (tool + TTS stub), and a cached-ack hit."""
    import tempfile

    from jarvis.adapters.audio.output import AudioOutputQueue
    from jarvis.adapters.fakes import EchoTTS, ScriptedModel
    from jarvis.adapters.tools.gateway import ToolGateway
    from jarvis.adapters.tools.windows import build_windows_tools
    from jarvis.adapters.tts.ack_cache import AckAudioCache, cache_key
    from jarvis.application.routing import Router
    from jarvis.application.turn_manager import TurnManager

    tmp = Path(tempfile.mkdtemp())
    (tmp / f"{cache_key('Hecho.')}.wav").write_bytes(silent_wav(0.4))
    first: list[float] = []

    async def render(turn_id: str, chunk: bytes) -> None:
        if not first or first[-1] is None:
            first[-1] = (time.perf_counter() - started[0]) * 1000

    started = [0.0]

    async def run(text: str, n: int) -> list[float]:
        gateway = ToolGateway(build_windows_tools())
        manager = TurnManager(router=Router(), tools=gateway.execute, model=ScriptedModel(), tts=EchoTTS(),
                              audio=AudioOutputQueue(render=render), ack_cache=AckAudioCache(tmp))
        out = []
        for _ in range(n):
            first.append(None)
            started[0] = time.perf_counter()
            await manager.handle(text)
            out.append(first[-1])
        return out

    RESULTS["command"] = {
        "time_query_to_first_audio_ms (tool+route, TTS stubbed)": stats(asyncio.run(run("qué hora es", 30))),
        "cached_ack_to_first_audio_ms": stats(asyncio.run(run("repite", 30))),
        "cached_ack_to_device_ms (real output stream, silent)": stats(asyncio.run(_cached_to_device(config))),
    }


async def _cached_to_device(config: Any, runs: int = 10) -> list[float]:
    """Cache hit -> first slice written to the real audio device (zeros: silent)."""
    from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
    from jarvis.adapters.tts.ack_cache import bytes_to_stream

    renderer = make_sounddevice_render(config.audio.output_device)
    real = renderer._blocking
    first: list[float] = []

    def blocking(turn_id: str, chunk: bytes) -> None:
        first.append(time.perf_counter())
        real(turn_id, chunk)

    renderer._blocking = blocking
    queue = AudioOutputQueue(render=renderer, render_timeout_seconds=30)
    out = []
    for _ in range(runs):
        first.clear()
        t = time.perf_counter()
        await queue.play(bytes_to_stream(silent_wav(0.3)), TurnContext.fresh("bench"))
        out.append((first[0] - t) * 1000)
    renderer.close()
    return out


# -- device timings (silent) --------------------------------------------------------------

def bench_interrupt(config: Any, trials: int = 8) -> None:
    from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
    from jarvis.adapters.tts.ack_cache import bytes_to_stream

    async def run() -> list[float]:
        renderer = make_sounddevice_render(config.audio.output_device)
        queue = AudioOutputQueue(render=renderer, render_timeout_seconds=30)
        out = []
        for _ in range(trials):
            context = TurnContext.fresh("bench")
            task = asyncio.create_task(queue.play(bytes_to_stream(silent_wav(3.0)), context))
            await asyncio.sleep(0.4)
            started = time.perf_counter()
            context.cancellation.cancel()
            queue.flush(context.trace_id)
            while queue.state()["current_turn"] is not None:
                await asyncio.sleep(0.001)
            out.append((time.perf_counter() - started) * 1000)
            await asyncio.gather(task, return_exceptions=True)
        renderer.close()
        return out

    RESULTS["interrupt"] = {"playback_stop_ms": stats(asyncio.run(run()))}


def bench_startup(config: Any, trials: int = 3) -> None:
    """Real mixer + real output queue, silent buffers; fake healthy services."""
    from jarvis.adapters.audio.mixer import Mixer
    from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
    from jarvis.adapters.tts.ack_cache import bytes_to_stream
    from jarvis.application.startup import StartupOptions, StartupSequence
    from jarvis.apps.daemon import startup_options
    from jarvis.core.contracts import HealthReport, HealthStatus

    base = startup_options(config)
    mixer = Mixer(config.audio.output_device)
    marks: dict[str, float] = {}
    real_render = mixer.render

    def render(frames: int) -> Any:
        out = real_render(frames)
        marks.setdefault("first_callback", time.perf_counter())
        return out * 0.0  # silence at the device, real timing

    mixer.render = render
    renderer = make_sounddevice_render(config.audio.output_device)
    queue = AudioOutputQueue(render=renderer, render_timeout_seconds=30)
    real_blocking = renderer._blocking

    def blocking(turn_id: str, chunk: bytes) -> None:
        marks.setdefault("welcome_audio", time.perf_counter())
        real_blocking(turn_id, chunk)

    renderer._blocking = blocking
    if base.music_path:
        mixer.load(base.music_path)  # warm cache, as the daemon preloads it
    results: dict[str, list[float]] = {"first_sound": [], "music_started": [], "welcome_audio": []}

    async def run() -> None:
        for _ in range(trials):
            marks.clear()
            options = StartupOptions(**{**base.__dict__, "music_volume": 0.0, "activation_sound": ""})
            healthy = [HealthReport(n, HealthStatus.HEALTHY) for n in options.essential]

            async def services() -> list:
                return healthy

            async def speak(text: str) -> None:
                raise AssertionError("live speech not expected")

            sequence = StartupSequence(
                options, mixer=mixer, speak=speak, start_services=services,
                cached_welcome=lambda text: silent_wav(1.0),
                play_audio=lambda audio: queue.play(bytes_to_stream(audio), TurnContext.fresh("welcome")),
                open_url=lambda url: None,
            )
            t0 = time.perf_counter()
            report = await sequence.trigger("bench")
            results["first_sound"].append((marks.get("first_callback", t0) - t0) * 1000)
            results["music_started"].append(report.timings_ms.get("music_started"))
            results["welcome_audio"].append((marks.get("welcome_audio", t0) - t0) * 1000)
            mixer.stop()
            await asyncio.sleep(0.5)

    asyncio.run(run())
    renderer.close()
    RESULTS["startup"] = {k + "_ms": stats(v) for k, v in results.items()} | {"welcome_delay_s": base.welcome_delay_seconds}


# -- models ----------------------------------------------------------------------------------

async def _load_clone(config: Any) -> tuple[Any, float]:
    from jarvis.adapters.tts.resolve import build_qwen_clone

    clone = build_qwen_clone(config)
    started = time.perf_counter()
    await clone.start()
    await asyncio.wait_for(clone.wait_ready(), 240)
    return clone, (time.perf_counter() - started) * 1000


def _gpu_used() -> float | None:
    from jarvis.adapters.tools.desktop import gpu_status

    status = gpu_status()
    return status["memory_used_mb"] if status else None


PHRASES = ["Hecho.", "Abriendo Visual Studio Code.", "Son las nueve y cuarto.", "Un momento, lo compruebo.",
           "La CPU está al doce por ciento.", "He abierto tu entorno de desarrollo.", "No he podido conectar.", "Listo."]


def bench_tts(config: Any, cold_runs: int = 1) -> None:
    async def run() -> dict[str, Any]:
        cold = []
        vram_before = _gpu_used()
        clone = None
        for i in range(cold_runs):
            if clone is not None:
                await clone.stop()
                await asyncio.sleep(3)
            clone, ms = await _load_clone(config)
            cold.append(ms)
        vram_warm = _gpu_used()
        ttfa, total = [], []
        for text in PHRASES * 2:
            started = time.perf_counter()
            first = None
            async for _ in clone.synthesize(one(text), TurnContext.fresh("bench")):
                first = first or time.perf_counter()
            ttfa.append((first - started) * 1000)
            total.append((time.perf_counter() - started) * 1000)
        info = clone.state().get("info", {})
        await clone.stop()
        return {"cold_load_ms": stats(cold), "warm_ttfa_ms": stats(ttfa), "warm_total_ms": stats(total),
                "vram_used_mb_before": vram_before, "vram_used_mb_warm": vram_warm,
                "worker_load_ms": info.get("load_ms"), "worker_warmup_ms": info.get("warmup_ms")}

    RESULTS["tts"] = asyncio.run(run())


def _utterance_wav(text: str) -> Path:
    """SAPI renders a Spanish command to a file (no playback)."""
    import comtypes.client
    import tempfile

    path = Path(tempfile.gettempdir()) / "jarvis_bench_utt.wav"
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()
    return path


def bench_stt(config: Any, runs: int = 10) -> None:
    from jarvis.adapters.stt.whisper import WhisperSTT

    stt = WhisperSTT(model=config.stt.model, language=config.stt.language, device=config.stt.device,
                     sample_rate=config.audio.sample_rate, hotwords=config.activation.wake_word)
    started = time.perf_counter()
    stt._load_model()
    load_ms = (time.perf_counter() - started) * 1000
    with wave.open(str(_utterance_wav("Jarvis, qué hora es")), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
    frames = [pcm[i:i + 3200] for i in range(0, len(pcm), 3200)]

    async def run() -> tuple[list[float], str]:
        times, text = [], ""
        for _ in range(runs):
            async def gen() -> AsyncIterator[bytes]:
                for frame in frames:
                    yield frame

            t = time.perf_counter()
            parts = [tr.text async for tr in stt.transcribe(gen(), TurnContext.fresh("bench"))]
            times.append((time.perf_counter() - t) * 1000)
            text = " ".join(parts)
        return times, text

    times, text = asyncio.run(run())
    RESULTS["stt"] = {"load_ms": round(load_ms), "transcribe_ms": stats(times), "audio_s": round(len(pcm) / 32000, 2), "text": text}
    RESULTS["_stt_instance"] = stt


def bench_llm(config: Any, runs: int = 8) -> None:
    from jarvis.application.runtime import _build_model_chain

    chain = _build_model_chain(config)
    prompts = ["¿Qué es un agujero negro? Una frase.", "Dime un dato curioso del mar.", "¿Cuánto es 17 por 3?",
               "Recomiéndame una película de ciencia ficción.", "¿Qué es Python?", "Saluda brevemente.",
               "¿Por qué el cielo es azul? Breve.", "Dame un consejo para concentrarme."]

    async def run() -> tuple[list[float], list[float], str]:
        ttft, total, provider = [], [], ""
        for prompt in prompts[:runs]:
            t = time.perf_counter()
            first = None
            async for token in chain.generate(prompt, TurnContext.fresh("bench")):
                if token and first is None:
                    first = time.perf_counter()
            ttft.append(((first or time.perf_counter()) - t) * 1000)
            total.append((time.perf_counter() - t) * 1000)
            provider = getattr(chain, "last_provider", "") or provider
        return ttft, total, provider

    ttft, total, provider = asyncio.run(run())
    RESULTS["llm"] = {"providers": [getattr(p, "name", "?") for p in chain.providers], "ttft_ms": stats(ttft), "total_ms": stats(total)}


def bench_e2e(config: Any, runs: int = 5) -> None:
    """End of utterance -> first TTS audio: STT + route + LLM + warm clone, playback stubbed."""
    from jarvis.adapters.audio.output import AudioOutputQueue
    from jarvis.adapters.tools.gateway import ToolGateway
    from jarvis.application.routing import Router
    from jarvis.application.runtime import _build_model_chain
    from jarvis.application.turn_manager import TurnManager

    stt = RESULTS.pop("_stt_instance", None)
    if stt is None:
        bench_stt(config, runs=1)
        stt = RESULTS.pop("_stt_instance")
    with wave.open(str(_utterance_wav("Jarvis, explícame qué es un agujero negro")), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
    frames = [pcm[i:i + 3200] for i in range(0, len(pcm), 3200)]

    async def run() -> dict[str, Any]:
        clone, _ = await _load_clone(config)
        first: list[float] = []

        async def render(turn_id: str, chunk: bytes) -> None:
            first.append(time.perf_counter())

        manager = TurnManager(router=Router(), tools=ToolGateway([]).execute, model=_build_model_chain(config),
                              tts=clone, audio=AudioOutputQueue(render=render))
        out, stt_ms = [], []
        for _ in range(runs):
            first.clear()

            async def gen() -> AsyncIterator[bytes]:
                for frame in frames:
                    yield frame

            t = time.perf_counter()
            text = " ".join([tr.text async for tr in stt.transcribe(gen(), TurnContext.fresh("bench"))])
            stt_ms.append((time.perf_counter() - t) * 1000)
            command = text.split(",", 1)[-1].strip() or text
            await manager.handle(command)
            out.append(((first[0] if first else time.perf_counter()) - t) * 1000)
        await clone.stop()
        return {"end_of_utterance_to_first_audio_ms": stats(out), "stt_ms": stats(stt_ms)}

    RESULTS["e2e"] = asyncio.run(run())


def bench_resources(config: Any) -> None:
    import psutil

    daemon = []
    for p in psutil.process_iter(["cmdline"]):
        if any("jarvis_daemon.pyw" in c for c in (p.info["cmdline"] or [])):
            daemon += [p] + p.children(recursive=True)
    daemon = list({p.pid: p for p in daemon}.values())
    for p in daemon:
        p.cpu_percent(None)
    time.sleep(10)
    RESULTS["resources"] = {
        "daemon_processes": [p.name() for p in daemon],
        "daemon_rss_mb": round(sum(p.memory_info().rss for p in daemon) / 2**20, 1),
        "daemon_cpu_percent_one_core": round(sum(p.cpu_percent(None) for p in daemon), 2),
        "gpu_used_mb": _gpu_used(),
    }


def bench_voice(config: Any) -> None:
    """Shared voice lifecycle: cold session, speculative head start, warm reuse."""
    from jarvis.adapters.tts.resolve import build_qwen_clone
    from jarvis.application.voice_manager import VoiceModelManager, VoicePolicy

    async def run() -> dict[str, Any]:
        manager = VoiceModelManager(lambda: build_qwen_clone(config), VoicePolicy(max_gpu_mb=0, speculative_min_interval_seconds=0))
        t = time.perf_counter()
        await manager.speculate("first clap")
        await asyncio.sleep(0.4)  # second clap ~0.4 s later
        await manager.acquire("session")
        await asyncio.wait_for(manager.tts.wait_ready(), 240)
        cold = (time.perf_counter() - t) * 1000
        vram_warm = _gpu_used()
        await manager.release("session")
        t = time.perf_counter()
        await manager.acquire("session")  # new session during cooldown
        await asyncio.wait_for(manager.tts.wait_ready(), 60)
        warm = (time.perf_counter() - t) * 1000
        first = None
        t = time.perf_counter()
        async for _ in manager.tts.synthesize(one("Hola."), TurnContext.fresh("bench")):
            first = first or time.perf_counter()
        ttfa = (first - t) * 1000
        await manager.release("session")
        await manager.shutdown()
        return {"cold_session_voice_ready_ms (speculative from 1st clap)": round(cold), "warm_session_voice_ready_ms": round(warm, 1),
                "warm_session_first_audio_ms": round(ttfa), "vram_used_mb_warm": vram_warm, "vram_used_mb_after_evict": _gpu_used(),
                "worker_processes_started": manager.loads}

    RESULTS["voice"] = asyncio.run(run())


SECTIONS = {
    "claps": bench_claps, "route": bench_route, "command": bench_command, "startup": bench_startup,
    "interrupt": bench_interrupt, "resources": bench_resources, "stt": bench_stt, "tts": bench_tts,
    "llm": bench_llm, "e2e": bench_e2e, "voice": bench_voice,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.win.json")
    parser.add_argument("--only", default=",".join(SECTIONS))
    parser.add_argument("--out")
    args = parser.parse_args()
    config = load_config(args.config)
    RESULTS["meta"] = {"host": platform.node(), "python": platform.python_version(), "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    for name in args.only.split(","):
        started = time.perf_counter()
        try:
            SECTIONS[name](config)
        except Exception as error:  # noqa: BLE001 - keep the other sections
            RESULTS[name] = {"error": f"{type(error).__name__}: {error}"}
        print(f"[{name}] {round(time.perf_counter() - started, 1)} s", file=sys.stderr, flush=True)
    RESULTS.pop("_stt_instance", None)
    text = json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
