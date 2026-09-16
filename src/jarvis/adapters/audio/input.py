"""Guarded microphone capture adapter (sounddevice, lazy-imported)."""

from __future__ import annotations

from typing import AsyncIterator, Optional

from jarvis.core.contracts import AudioCapture, TurnContext
from jarvis.core.errors import ProviderUnavailable


class MicCapture:
    """Capture int16 PCM frames from the default (or selected) input device."""

    def __init__(self, *, sample_rate: int = 16000, channels: int = 1, device: Optional[int] = None) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

    async def capture(self, context: TurnContext) -> AsyncIterator[bytes]:
        try:
            import sounddevice as sd  # noqa: PLC0415
        except ImportError as error:
            raise ProviderUnavailable(
                "sounddevice is not installed; audio capture unavailable", provider="audio"
            ) from error

        import numpy as np  # noqa: PLC0415

        def _stream():
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                device=self.device,
                dtype="int16",
            ) as stream:
                while True:
                    frames, _ = stream.read(self.sample_rate // 10)
                    yield frames.tobytes()

        import asyncio

        loop = asyncio.get_running_loop()

        async def _capture() -> AsyncIterator[bytes]:
            iterator = _stream()
            while not context.cancellation.cancelled:
                chunk = await loop.run_in_executor(None, next, iterator)
                yield chunk

        async for chunk in _capture():
            yield chunk


def list_devices() -> list[dict[str, object]]:
    """Return available PortAudio devices, or an empty list when unavailable."""
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        return []
    devices: list[dict[str, object]] = []
    for index, device in enumerate(sd.query_devices()):
        devices.append(
            {
                "index": index,
                "name": device.get("name"),
                "max_input_channels": int(device.get("max_input_channels", 0)),
                "max_output_channels": int(device.get("max_output_channels", 0)),
            }
        )
    return devices
