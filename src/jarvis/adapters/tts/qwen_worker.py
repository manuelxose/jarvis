"""Faster Qwen3-TTS voice-cloning worker (runs in the isolated TTS venv).

Jarvis pins transformers 4.x (coqui-tts); faster-qwen3-tts needs 5.x, so this
script runs as a supervised child process with its own interpreter and must
not import ``jarvis``. It speaks JSON lines over stdio, like the Hermes child:

stdin  {"op": "synth", "id": str, "text": str}
       {"op": "cancel", "id": str}   {"op": "ping"}   {"op": "exit"}
stdout {"type": "ready", ...}  {"type": "audio", "id", "pcm": base64 int16 mono}
       {"type": "done", "id", "ttfa_ms", "audio_s", "gen_ms", "cancelled"}
       {"type": "error", "id", "code", "detail"}  {"type": "pong", ...}

Subcommands: ``serve`` (default), ``prepare`` (build the cached voice prompt
for an enrolled profile and write a preview), ``bench`` (Phase B spike).
The reference voice never leaves this machine: nothing here opens a socket.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
PREVIEW_TEXT = (
    "Hola, soy Jarvis. Esta es una prueba de mi voz en español: "
    "son las diez y cuarto y mañana lloverá en Madrid."
)

# The protocol owns the real stdout; library prints go to stderr.
_OUT = sys.stdout
sys.stdout = sys.stderr
_OUT_LOCK = threading.Lock()


def emit(event: dict) -> None:
    """Write one event as a JSON line on stdout (thread-safe)."""
    line = json.dumps(event, separators=(",", ":"))
    with _OUT_LOCK:
        _OUT.write(line + "\n")
        _OUT.flush()


def load_model(model_name: str, device: str):
    """Load Faster Qwen3-TTS in bfloat16 on *device*."""
    import torch  # noqa: PLC0415
    from faster_qwen3_tts import FasterQwen3TTS  # noqa: PLC0415

    return FasterQwen3TTS.from_pretrained(model_name, device=device, dtype=torch.bfloat16)


def _profile_meta(profile_dir: Path) -> dict | None:
    meta_path = profile_dir / "profile.json"
    if not (profile_dir / "reference.wav").is_file() or not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _to_device(value, device: str):
    if isinstance(value, list):
        return [_to_device(item, device) for item in value]
    return value.to(device) if hasattr(value, "to") else value


def _to_cpu(value):
    if isinstance(value, list):
        return [_to_cpu(item) for item in value]
    return value.detach().cpu() if hasattr(value, "detach") else value


def build_prompt(model, profile_dir: Path, meta: dict) -> dict:
    """Encode the reference once and persist it; later starts reuse prompt.pt.

    The cache is keyed on model + mode + reference text; the upstream library
    keeps its own prompt cache only in memory, so without prompt.pt every
    process restart re-encodes the reference audio.
    """
    import torch  # noqa: PLC0415

    key = {"model": meta["model"], "mode": meta["mode"], "ref_text": meta.get("ref_text", "")}
    cache = profile_dir / "prompt.pt"
    if cache.is_file():
        saved = torch.load(cache, weights_only=False)
        if saved.get("key") == key:
            return _to_device(saved["prompt"], "cuda")
    xvec = meta["mode"] == "xvec"
    ref = str(profile_dir / "reference.wav")
    if xvec:
        items = model.model.create_voice_clone_prompt(ref_audio=ref, ref_text="", x_vector_only_mode=True)
        prompt = {
            "ref_code": [None],
            "ref_spk_embedding": [items[0].ref_spk_embedding],
            "x_vector_only_mode": [True],
            "icl_mode": [False],
        }
    else:
        audio = model._load_ref_audio_with_silence(ref, silence_secs=0.5)
        items = model.model.create_voice_clone_prompt(ref_audio=audio, ref_text=meta["ref_text"])
        prompt = model.model._prompt_items_to_voice_clone_prompt(items)
    torch.save({"key": key, "prompt": {k: _to_cpu(v) for k, v in prompt.items()}}, cache)
    return prompt


class Engine:
    """The loaded model plus the owner's cached voice conditioning; streams audio chunks for a text."""

    def __init__(self, model, prompt: dict | None, meta: dict | None, language: str, chunk_size: int) -> None:
        self.model = model
        self.prompt = prompt
        self.meta = meta or {}
        self.language = language
        self.chunk_size = chunk_size

    def stream(self, text: str):
        """Yield ``(float32 mono ndarray, sample_rate)`` chunks as they are generated."""
        kwargs = dict(
            text=text,
            language=self.language,
            chunk_size=self.chunk_size,
            voice_clone_prompt=self.prompt,
            ref_text=self.meta.get("ref_text", ""),
        )
        for audio, sr, _timing in self.model.generate_voice_clone_streaming(**kwargs):
            yield audio, sr


def _pcm16(audio) -> bytes:
    import numpy as np  # noqa: PLC0415

    clipped = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def _vram() -> dict:
    import torch  # noqa: PLC0415

    free, total = torch.cuda.mem_get_info()
    return {
        "vram_free_mb": free // 2**20,
        "vram_total_mb": total // 2**20,
        "vram_peak_alloc_mb": torch.cuda.max_memory_allocated() // 2**20,
        "vram_reserved_mb": torch.cuda.memory_reserved() // 2**20,
    }


def serve(args) -> int:
    """Worker main loop: load the model, report ``ready``, then synthesize requests from stdin until EOF."""
    import torch  # noqa: PLC0415

    started = time.monotonic()
    if not torch.cuda.is_available():
        emit({"type": "fatal", "code": "no_cuda", "detail": "CUDA is unavailable to the TTS worker"})
        return 2
    free_mb = torch.cuda.mem_get_info()[0] // 2**20
    if free_mb < args.min_free_vram_mb:
        emit({"type": "fatal", "code": "low_vram", "detail": f"only {free_mb} MiB GPU memory free"})
        return 3
    profile_dir = Path(args.profile_dir) if args.profile_dir else None
    meta = _profile_meta(profile_dir) if profile_dir else None
    if meta is None:
        # Nothing to clone: do not hold ~3 GB of VRAM just to refuse requests.
        emit({"type": "ready", "profile": None, "mode": None, "model": args.model, **_vram()})
        return _refuse_all()
    try:
        model = load_model(args.model, "cuda")
        prompt = build_prompt(model, profile_dir, meta) if meta else None
        load_ms = (time.monotonic() - started) * 1000
        engine = Engine(model, prompt, meta, args.language, args.chunk_size)
        warm_started = time.monotonic()
        model.warmup(prefill_len=100)
        if prompt is not None:
            for _ in engine.stream("Hola."):
                pass
        warm_ms = (time.monotonic() - warm_started) * 1000
    except torch.cuda.OutOfMemoryError as error:
        emit({"type": "fatal", "code": "oom", "detail": str(error)[:300]})
        return 3
    emit({
        "type": "ready",
        "profile": meta.get("name", "owner") if meta else None,
        "mode": meta.get("mode") if meta else None,
        "model": args.model,
        "load_ms": round(load_ms),
        "warmup_ms": round(warm_ms),
        **_vram(),
    })

    requests: queue.Queue = queue.Queue()
    cancelled: set[str] = set()

    def reader() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            op = message.get("op")
            if op == "cancel":
                cancelled.add(str(message.get("id")))
            elif op == "ping":
                emit({"type": "pong", **_vram()})
            else:
                requests.put(message)
        requests.put({"op": "exit"})

    threading.Thread(target=reader, daemon=True).start()
    while True:
        message = requests.get()
        if message.get("op") == "exit":
            return 0
        if message.get("op") != "synth":
            continue
        request_id = str(message.get("id"))
        if request_id in cancelled:
            cancelled.discard(request_id)
            emit({"type": "done", "id": request_id, "cancelled": True})
            continue
        if prompt is None:
            emit({"type": "error", "id": request_id, "code": "no_profile", "detail": "no voice profile enrolled"})
            continue
        t0 = time.monotonic()
        ttfa = None
        samples = 0
        rate = 24000
        was_cancelled = False
        generator = engine.stream(str(message.get("text", "")))
        try:
            for audio, rate in generator:
                if request_id in cancelled:
                    was_cancelled = True
                    break
                if ttfa is None:
                    ttfa = (time.monotonic() - t0) * 1000
                pcm = _pcm16(audio)
                samples += len(pcm) // 2
                emit({"type": "audio", "id": request_id, "rate": rate, "pcm": base64.b64encode(pcm).decode("ascii")})
        except torch.cuda.OutOfMemoryError as error:
            torch.cuda.empty_cache()
            emit({"type": "error", "id": request_id, "code": "oom", "detail": str(error)[:300]})
            continue
        except Exception as error:  # a bad segment must not kill the worker
            emit({"type": "error", "id": request_id, "code": "synth_failed", "detail": repr(error)[:300]})
            continue
        finally:
            generator.close()
        cancelled.discard(request_id)
        gen_ms = (time.monotonic() - t0) * 1000
        emit({
            "type": "done",
            "id": request_id,
            "cancelled": was_cancelled,
            "ttfa_ms": round(ttfa or 0, 1),
            "gen_ms": round(gen_ms, 1),
            "audio_s": round(samples / rate, 3),
        })


def _refuse_all() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("op") == "exit":
            return 0
        if message.get("op") == "synth":
            emit({"type": "error", "id": str(message.get("id")), "code": "no_profile", "detail": "no voice profile enrolled"})
        elif message.get("op") == "ping":
            emit({"type": "pong"})
    return 0


def prepare(args) -> int:
    """Build prompt.pt for an enrolled profile and write preview.wav."""
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    profile_dir = Path(args.profile_dir)
    meta = _profile_meta(profile_dir)
    if meta is None:
        print(f"no enrolled profile in {profile_dir}", file=sys.stderr)
        return 1
    meta.setdefault("model", args.model)
    model = load_model(meta["model"], "cuda")
    prompt = build_prompt(model, profile_dir, meta)
    engine = Engine(model, prompt, meta, args.language, args.chunk_size)
    parts, rate = [], 24000
    for audio, rate in engine.stream(args.text or PREVIEW_TEXT):
        parts.append(np.asarray(audio, dtype=np.float32).reshape(-1))
    sf.write(str(profile_dir / "preview.wav"), np.concatenate(parts), rate)
    emit({"type": "prepared", "preview": str(profile_dir / "preview.wav"), **_vram()})
    return 0


BENCH_TEXTS = [
    "Claro, ahora mismo lo miro.",
    "Mañana en Madrid habrá cielos despejados y una máxima de veintiocho grados.",
    "He abierto Spotify y he puesto tu lista de música para concentrarte.",
    "Son las diez y cuarto. Tienes una reunión a las once con el equipo de diseño.",
    "No he encontrado ese archivo; ¿quieres que lo busque en la carpeta de descargas?",
]


def bench(args) -> int:
    """Measure cold load, warmup, TTFA, RTF and peak VRAM for one profile/config."""
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415
    import torch  # noqa: PLC0415

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir)
    meta = _profile_meta(profile_dir)
    if meta is None:
        print(f"no enrolled profile in {profile_dir}", file=sys.stderr)
        return 1
    torch.cuda.reset_peak_memory_stats()
    free_before = torch.cuda.mem_get_info()[0] // 2**20
    t0 = time.monotonic()
    model = load_model(meta["model"], "cuda")
    load_ms = (time.monotonic() - t0) * 1000
    t1 = time.monotonic()
    prompt = build_prompt(model, profile_dir, meta)
    prompt_ms = (time.monotonic() - t1) * 1000
    engine = Engine(model, prompt, meta, args.language, args.chunk_size)
    t2 = time.monotonic()
    model.warmup(prefill_len=100)
    first = None
    for _ in engine.stream(BENCH_TEXTS[0]):
        first = first or (time.monotonic() - t2) * 1000
    warmup_ms = (time.monotonic() - t2) * 1000
    trials = []
    for index in range(args.trials):
        time.sleep(args.pause)  # conversational duty cycle; 0 = back-to-back stress
        text = BENCH_TEXTS[index % len(BENCH_TEXTS)]
        start = time.monotonic()
        ttfa, parts, rate = None, [], 24000
        for audio, rate in engine.stream(text):
            ttfa = ttfa if ttfa is not None else (time.monotonic() - start) * 1000
            parts.append(np.asarray(audio, dtype=np.float32).reshape(-1))
        wall = time.monotonic() - start
        audio = np.concatenate(parts)
        seconds = len(audio) / rate
        trials.append({"text": index % len(BENCH_TEXTS), "ttfa_ms": round(ttfa, 1), "wall_s": round(wall, 3),
                       "audio_s": round(seconds, 3), "rtf": round(seconds / wall, 3)})
        if index < len(BENCH_TEXTS):
            sf.write(str(out / f"sample_{meta['mode']}_c{args.chunk_size}_{index}.wav"), audio, rate)
    ttfas = sorted(t["ttfa_ms"] for t in trials)
    rtfs = sorted(t["rtf"] for t in trials)

    def pct(values, p):
        return values[min(len(values) - 1, round(p / 100 * (len(values) - 1)))]

    result = {
        "model": meta["model"], "mode": meta["mode"], "chunk_size": args.chunk_size, "pause_s": args.pause,
        "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__, "cuda": torch.version.cuda,
        "vram_free_before_mb": free_before, "cold_load_ms": round(load_ms), "prompt_ms": round(prompt_ms),
        "warmup_ms": round(warmup_ms), "first_synth_ttfa_ms": round(first or 0),
        "ttfa_ms": {"p50": pct(ttfas, 50), "p95": pct(ttfas, 95), "max": ttfas[-1], "min": ttfas[0]},
        "rtf": {"p50": pct(rtfs, 50), "min": rtfs[0]},
        "trials": trials, **_vram(),
    }
    (out / f"bench_{meta['mode']}_c{args.chunk_size}_p{args.pause:g}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    emit({"type": "bench", **{k: v for k, v in result.items() if k != "trials"}})
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: ``serve`` (default), ``prepare`` or ``bench``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "prepare", "bench"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--profile-dir", default=os.environ.get("JARVIS_VOICE_PROFILE", ""))
    parser.add_argument("--language", default="Spanish")
    parser.add_argument("--chunk-size", type=int, default=4)
    parser.add_argument("--min-free-vram-mb", type=int, default=1500)
    parser.add_argument("--text", default="")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--out", default="bench-tts")
    parser.add_argument("--pause", type=float, default=0.0)
    args = parser.parse_args(argv)
    return {"serve": serve, "prepare": prepare, "bench": bench}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
