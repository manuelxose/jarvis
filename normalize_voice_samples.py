"""
normalize_voice_samples.py
==========================
Normaliza los archivos en voice_samples/:

  1. Detecta todos los archivos de audio (wav, mp3, m4a, ogg, flac, aac, …).
  2. Convierte los que NO son WAV usando pydub (requiere ffmpeg en PATH).
  3. Renombra todos los WAV resultantes a sample1.wav, sample2.wav, … en
     orden alfabético del nombre original, conservando las muestras que ya
     se llamen correctamente sin tocarlas más de lo necesario.
  4. Elimina los archivos de audio originales que hayan sido convertidos/
     sustituidos correctamente para no dejar duplicados.

Uso autónomo::

    python normalize_voice_samples.py [--dir voice_samples] [--sr 22050] [--channels 1]

También se puede importar y llamar a ``run()`` desde setup.py.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# Formatos de audio reconocidos (extensiones sin punto, en minúsculas)
AUDIO_EXTENSIONS: set[str] = {
    "wav", "mp3", "m4a", "aac", "ogg", "flac", "opus", "wma", "aiff", "aif",
}

# Patrón de nombre canónico: sample<N>.wav
_CANONICAL_RE = re.compile(r"^sample(\d+)\.wav$", re.IGNORECASE)


def _print(msg: str) -> None:
    print(f"[NORMALIZE] {msg}")


def _load_pydub():
    """Importa pydub; lanza RuntimeError con consejo si no está instalado."""
    try:
        from pydub import AudioSegment  # noqa: PLC0415
        return AudioSegment
    except ImportError as exc:
        raise RuntimeError(
            "pydub no está instalado. Ejecuta: pip install pydub\n"
            "También necesitas ffmpeg instalado y en el PATH del sistema."
        ) from exc


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def convert_to_wav(
    src: Path,
    dst: Path,
    sample_rate: int = 22050,
    channels: int = 1,
) -> None:
    """Convierte *src* a WAV PCM y lo guarda en *dst*."""
    AudioSegment = _load_pydub()

    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg no se encontró en el PATH. Instálalo desde https://ffmpeg.org/download.html"
        )

    fmt = src.suffix.lstrip(".").lower()
    audio = AudioSegment.from_file(str(src), format=fmt)
    audio = audio.set_frame_rate(sample_rate).set_channels(channels)
    audio.export(str(dst), format="wav")


def collect_audio_files(samples_dir: Path) -> list[Path]:
    """Devuelve todos los archivos de audio en *samples_dir* ordenados por nombre."""
    files = [
        f for f in samples_dir.iterdir()
        if f.is_file() and f.suffix.lstrip(".").lower() in AUDIO_EXTENSIONS
    ]
    return sorted(files, key=lambda f: f.name.lower())


def run(
    samples_dir: Path | None = None,
    sample_rate: int = 22050,
    channels: int = 1,
) -> int:
    """
    Punto de entrada principal.

    Returns
    -------
    int
        Número de archivos WAV disponibles tras la normalización.
    """
    if samples_dir is None:
        samples_dir = Path(__file__).resolve().parent / "voice_samples"

    if not samples_dir.exists():
        raise RuntimeError(f"No se encontró el directorio: {samples_dir}")

    all_audio = collect_audio_files(samples_dir)

    if not all_audio:
        _print("No se encontraron archivos de audio en voice_samples/.")
        return 0

    _print(f"Archivos de audio detectados: {[f.name for f in all_audio]}")

    # --- Paso 1: convertir los que no son WAV ----------------------------------
    converted: list[Path] = []   # archivos WAV resultantes (pueden ser nuevos o ya existentes)
    originals_to_delete: list[Path] = []

    for src in all_audio:
        ext = src.suffix.lstrip(".").lower()
        if ext == "wav":
            converted.append(src)
        else:
            # Temporal WAV con el mismo stem para no colisionar
            tmp_wav = src.with_suffix(".wav")
            if tmp_wav.exists():
                # Evitar sobreescribir una muestra WAV ya válida con el mismo nombre
                tmp_wav = src.with_name(f"_converted_{src.stem}.wav")

            _print(f"Convirtiendo {src.name} -> {tmp_wav.name} ...")
            convert_to_wav(src, tmp_wav, sample_rate=sample_rate, channels=channels)
            converted.append(tmp_wav)
            originals_to_delete.append(src)

    # --- Paso 2: renombrar todos los WAV a sample1.wav, sample2.wav, … --------
    # Ordenamos de nuevo para tener un orden determinista
    converted_sorted = sorted(converted, key=lambda f: f.name.lower())

    # Construimos el mapeo final: archivo actual -> nombre canónico deseado
    rename_plan: list[tuple[Path, Path]] = []
    for idx, wav_file in enumerate(converted_sorted, start=1):
        canonical_name = f"sample{idx}.wav"
        canonical_path = samples_dir / canonical_name
        if wav_file.name != canonical_name:
            rename_plan.append((wav_file, canonical_path))
        # Si ya tiene el nombre correcto, no hay nada que hacer

    if rename_plan:
        _print("Plan de renombrado:")
        for src, dst in rename_plan:
            _print(f"  {src.name}  ->  {dst.name}")

        # Fase de renombrado en dos pasos para evitar colisiones (e.g. sample2 -> sample1)
        # Paso A: renombrar a nombres temporales únicos
        temp_paths: list[tuple[Path, Path]] = []
        for src, dst in rename_plan:
            tmp = src.with_name(f"_tmp_{src.name}")
            src.rename(tmp)
            temp_paths.append((tmp, dst))

        # Paso B: renombrar de temporales a nombres canónicos
        for tmp, dst in temp_paths:
            if dst.exists() and dst != tmp:
                dst.unlink()
            tmp.rename(dst)
    else:
        _print("Todos los archivos ya tienen nombres canónicos. Nada que renombrar.")

    # --- Paso 3: eliminar originales no-WAV ya convertidos --------------------
    for orig in originals_to_delete:
        try:
            orig.unlink()
            _print(f"Eliminado original convertido: {orig.name}")
        except FileNotFoundError:
            pass  # ya no existe, sin problema

    # --- Resumen --------------------------------------------------------------
    final_wavs = sorted(samples_dir.glob("sample*.wav"))
    _print(f"Normalización completa. Muestras WAV disponibles: {[f.name for f in final_wavs]}")
    return len(final_wavs)


# ---------------------------------------------------------------------------
# Ejecución autónoma
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normaliza los archivos de audio de voice_samples/ a WAV y los renombra como sample1.wav, sample2.wav, …"
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Ruta al directorio voice_samples (por defecto: <repo>/voice_samples)",
    )
    parser.add_argument(
        "--sr",
        type=int,
        default=22050,
        help="Sample rate de salida en Hz (por defecto: 22050)",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=1,
        choices=[1, 2],
        help="Número de canales: 1 (mono) o 2 (estéreo). Por defecto: 1",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        count = run(samples_dir=args.dir, sample_rate=args.sr, channels=args.channels)
        if count == 0:
            print("[NORMALIZE][ADVERTENCIA] No quedaron muestras WAV tras la normalización.")
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"[NORMALIZE][ERROR] {exc}")
        sys.exit(1)
