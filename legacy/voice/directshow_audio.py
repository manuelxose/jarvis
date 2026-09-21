"""Windows DirectShow microphone capture through the FFmpeg installed by bootstrap."""
from __future__ import annotations

import re
import shutil
import subprocess


_QUOTED_DEVICE = re.compile(r'\]\s+"([^"]+)"')
_GENERIC_TOKENS = {
    "audio",
    "free",
    "hands",
    "headset",
    "input",
    "microphone",
    "mic",
}


class DirectShowCaptureError(RuntimeError):
    pass


def parse_audio_devices(output: str) -> list[str]:
    """Extract audio endpoint names from ``ffmpeg -f dshow -list_devices`` output."""
    devices: list[str] = []
    in_audio_section = False
    for line in output.splitlines():
        normalized = line.lower()
        if "directshow audio devices" in normalized:
            in_audio_section = True
            continue
        if in_audio_section and "directshow" in normalized and "devices" in normalized:
            break
        if not in_audio_section or "alternative name" in normalized:
            continue
        match = _QUOTED_DEVICE.search(line)
        if match:
            devices.append(match.group(1))
    return devices


def choose_audio_device(devices: list[str], preferred_name: str | None) -> str:
    """Prefer the DirectShow endpoint that matches PortAudio's selected input."""
    if not devices:
        raise DirectShowCaptureError("FFmpeg DirectShow no enumero ningun microfono.")
    if not preferred_name:
        return devices[0]
    preferred_tokens = _device_tokens(preferred_name)
    matched = max(devices, key=lambda device: len(preferred_tokens & _device_tokens(device)))
    return matched if preferred_tokens & _device_tokens(matched) else devices[0]


def list_audio_devices(ffmpeg: str | None = None) -> list[str]:
    executable = ffmpeg or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not executable:
        raise DirectShowCaptureError("No se encontro ffmpeg en PATH.")
    result = subprocess.run(
        [executable, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    devices = parse_audio_devices(f"{result.stdout}\n{result.stderr}")
    if not devices:
        detail = (result.stderr or result.stdout).strip()
        raise DirectShowCaptureError(
            f"FFmpeg DirectShow no encontro entradas de audio: {detail[-2000:]}"
        )
    return devices


def capture_pcm16(
    device: str,
    duration_seconds: float,
    sample_rate: int = 16000,
    ffmpeg: str | None = None,
) -> bytes:
    """Capture mono signed-16-bit PCM without using PortAudio."""
    executable = ffmpeg or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not executable:
        raise DirectShowCaptureError("No se encontro ffmpeg en PATH.")
    try:
        result = subprocess.run(
            [
                executable,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-f",
                "dshow",
                "-audio_buffer_size",
                "50",
                "-i",
                f"audio={device}",
                "-t",
                f"{duration_seconds:.3f}",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=max(10, int(duration_seconds) + 8),
        )
    except subprocess.TimeoutExpired as exc:
        raise DirectShowCaptureError("FFmpeg DirectShow agoto el tiempo de captura.") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise DirectShowCaptureError(f"FFmpeg DirectShow no pudo abrir {device!r}: {detail}")
    if not result.stdout:
        raise DirectShowCaptureError(f"FFmpeg DirectShow no recibio audio de {device!r}.")
    return result.stdout


def capture_first_available(
    preferred_name: str | None,
    duration_seconds: float,
    sample_rate: int = 16000,
    ffmpeg: str | None = None,
) -> tuple[str, bytes]:
    """Capture from the matching endpoint first, then other available inputs."""
    devices = list_audio_devices(ffmpeg)
    preferred = choose_audio_device(devices, preferred_name)
    errors: list[str] = []
    for device in [preferred, *(item for item in devices if item != preferred)]:
        try:
            return device, capture_pcm16(device, duration_seconds, sample_rate, ffmpeg)
        except DirectShowCaptureError as exc:
            errors.append(str(exc))
    raise DirectShowCaptureError("; ".join(errors))


def _device_tokens(name: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", name.casefold())
        if token not in _GENERIC_TOKENS
    }
