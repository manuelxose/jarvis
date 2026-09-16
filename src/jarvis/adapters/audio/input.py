"""Guarded microphone capture adapter (sounddevice, lazy-imported)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from typing import AsyncIterator, Generator

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderUnavailable


class MicCapture:
    """Capture int16 PCM frames from the default (or selected) input device."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int | str | None = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if channels <= 0:
            raise ValueError("channels must be positive")
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

        def _stream() -> Generator[bytes, None, None]:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                device=self.device,
                dtype="int16",
            ) as stream:
                while True:
                    frames, _ = stream.read(self.sample_rate // 10)
                    yield frames.tobytes()

        def _next_frame(iterator: Generator[bytes, None, None]) -> tuple[bool, bytes]:
            try:
                return True, next(iterator)
            except StopIteration:
                return False, b""

        loop = asyncio.get_running_loop()
        iterator = _stream()
        try:
            while not context.cancellation.cancelled:
                try:
                    has_frame, chunk = await loop.run_in_executor(None, _next_frame, iterator)
                except Exception as error:
                    raise ProviderUnavailable(
                        "sounddevice capture failed; check the selected input device", provider="audio"
                    ) from error
                if not has_frame:
                    return
                if not context.cancellation.cancelled and chunk:
                    yield chunk
        finally:
            with suppress(Exception):
                iterator.close()


def list_devices() -> list[dict[str, object]]:
    """Return available PortAudio devices, or an empty list when unavailable."""
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        return []
    try:
        reported_devices = sd.query_devices()
    except Exception:
        return []

    try:
        device_entries = iter(reported_devices)
    except TypeError:
        return []

    devices: list[dict[str, object]] = []
    for index, device in enumerate(device_entries):
        if not isinstance(device, Mapping):
            continue
        try:
            input_channels = int(device.get("max_input_channels", 0))
            output_channels = int(device.get("max_output_channels", 0))
        except (TypeError, ValueError):
            continue
        devices.append(
            {
                "index": index,
                "name": device.get("name"),
                "max_input_channels": input_channels,
                "max_output_channels": output_channels,
            }
        )
    return devices
