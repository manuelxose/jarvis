"""One shared cloned-voice model with an adaptive lifecycle.

States::

    cold ──speculate (1st clap / wake word)──► speculative ──2nd clap──► warm
      ▲                        │ expiry (candidate cancelled)            │ session ends
      └──────── evict ◄────────┘                                         ▼
      ▲                                                               cooldown ──new session──► warm
      └──── evict: cooldown timeout, GPU pressure, GPU-heavy app ◄────────┘

There is exactly one :class:`QwenCloneTTS` object (one worker process, one
model in VRAM); every session's runtime receives the same instance. Holders
(active sessions) pin the model: it is never evicted while held or while a
synthesis request is in flight. ``preload`` modes: ``adaptive`` (the above),
``always`` (load at start, never evict) and ``on_demand`` (no speculation,
evict as soon as the last session ends).
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from jarvis.observability.event_hub import hub

logger = logging.getLogger("jarvis.voice")

_MODES = ("adaptive", "always", "on_demand")


class VoiceState(str, enum.Enum):
    COLD = "cold"  # not loaded (also after eviction)
    SPECULATIVE = "speculative"  # loading/loaded on a first clap, not yet confirmed
    WARM = "warm"  # held by a session
    COOLDOWN = "cooldown"  # loaded, unused, waiting for reuse or eviction


@dataclass(frozen=True)
class VoicePolicy:
    preload: str = "adaptive"
    speculative: bool = True
    predictive_on_wake_word: bool = True
    cooldown_seconds: float = 600.0
    # Only (pre)load when at least this much VRAM is free (the clone needs ~4.2 GB).
    max_gpu_mb: float = 4500.0
    # Evict an idle model when free VRAM drops under this (another app wants it).
    evict_on_pressure: bool = True
    min_free_vram_mb: float = 700.0
    # Executables whose presence means "GPU belongs to them" (games, renders).
    gpu_busy_processes: tuple[str, ...] = ()
    speculative_min_interval_seconds: float = 20.0

    @classmethod
    def from_config(cls, data: Mapping[str, Any]) -> "VoicePolicy":
        values = dict(data)
        if "gpu_busy_processes" in values:
            values["gpu_busy_processes"] = tuple(str(p).lower() for p in values["gpu_busy_processes"])
        policy = cls(**values)
        if policy.preload not in _MODES:
            raise ValueError(f"voice.preload must be one of {_MODES}")
        if policy.cooldown_seconds < 0 or policy.max_gpu_mb < 0 or policy.min_free_vram_mb < 0:
            raise ValueError("voice timeouts and budgets must not be negative")
        return policy


def _running_process_names() -> set[str]:
    try:
        import psutil  # noqa: PLC0415

        return {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
    except Exception:  # noqa: BLE001 - no psutil: no busy detection
        return set()


def _default_free_vram() -> Optional[float]:
    from jarvis.adapters.tools.desktop import gpu_free_mb  # noqa: PLC0415

    return gpu_free_mb()


class VoiceModelManager:
    def __init__(
        self,
        factory: Callable[[], Any],
        policy: VoicePolicy | None = None,
        *,
        free_vram: Callable[[], Optional[float]] = _default_free_vram,
        running_processes: Callable[[], set[str]] = _running_process_names,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self.policy = policy or VoicePolicy()
        self._free_vram = free_vram
        self._running = running_processes
        self._clock = clock
        self._tts: Any = None
        self._created = False
        self.state = VoiceState.COLD
        self._holders: set[str] = set()
        self._started = False
        self._speculated_at = -1e9
        self._speculative_start = False  # this speculation started the worker
        self._evict_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self.last_reason = ""
        self.loads = 0

    # -- the shared instance -------------------------------------------------
    @property
    def tts(self) -> Any:
        """The single shared TTS object (created lazily; ``None`` if not configured)."""
        if not self._created:
            self._tts, self._created = self._factory(), True
        return self._tts

    @property
    def holders(self) -> frozenset[str]:
        return frozenset(self._holders)

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state.value, "holders": sorted(self._holders), "loads": self.loads, "last_reason": self.last_reason, "preload": self.policy.preload}

    # -- policy checks --------------------------------------------------------
    async def _gpu_blocked(self) -> str:
        # nvidia-smi / process scans take ~100 ms: never on the event loop.
        busy = set(self.policy.gpu_busy_processes) & await asyncio.to_thread(self._running)
        if busy:
            return f"GPU-heavy app running ({', '.join(sorted(busy))})"
        free = await asyncio.to_thread(self._free_vram)
        if not self._started and free is not None and free < self.policy.max_gpu_mb:
            return f"only {free:.0f} MiB VRAM free (< {self.policy.max_gpu_mb:.0f})"
        return ""

    def _set(self, state: VoiceState, reason: str) -> None:
        if state is not self.state:
            logger.info("voice %s -> %s (%s)", self.state.value, state.value, reason)
            self.state, self.last_reason = state, reason
            hub.publish("voice.state", state=state.value, reason=reason)

    async def _start(self, reason: str) -> bool:
        tts = self.tts
        if tts is None:
            return False
        if not self._started:
            await tts.start()
            self._started, self.loads = True, self.loads + 1
            hub.publish("voice.loading", reason=reason)
            asyncio.ensure_future(self._announce_ready())
        return True

    async def _announce_ready(self) -> None:
        started = self._clock()
        try:
            info = await self.tts.wait_ready()
        except Exception as error:  # noqa: BLE001 - the chain falls back to SAPI
            if self._started:  # not a load we cancelled ourselves (e.g. a stray clap)
                logger.warning("voice model failed to load: %s", error)
            return
        hub.publish("voice.ready", load_ms=info.get("load_ms", round((self._clock() - started) * 1000)))

    # -- lifecycle API --------------------------------------------------------
    async def start_always(self) -> None:
        if self.policy.preload == "always":
            async with self._lock:
                if await self._start("preload=always"):
                    self._set(VoiceState.COOLDOWN, "preload=always")

    async def speculate(self, reason: str) -> bool:
        """First clap / wake word: begin loading without committing. Returns True if loading."""
        async with self._lock:
            if self.policy.preload != "adaptive" or not self.policy.speculative:
                return False
            if self._started:
                return True  # already loaded (cooldown/warm): nothing to do, nothing to undo
            if self._clock() - self._speculated_at < self.policy.speculative_min_interval_seconds:
                return False  # stray single claps must not thrash the GPU
            blocked = await self._gpu_blocked()
            if blocked:
                logger.info("no speculative voice load: %s", blocked)
                return False
            self._speculated_at = self._clock()
            if not await self._start(reason):
                return False
            self._speculative_start = True
            self._set(VoiceState.SPECULATIVE, reason)
            return True

    async def cancel_speculation(self, reason: str) -> None:
        """The candidate expired: undo a load that this speculation started."""
        async with self._lock:
            if self.state is VoiceState.SPECULATIVE and not self._holders and self._speculative_start:
                await self._evict_locked(f"speculation cancelled: {reason}")

    async def acquire(self, owner: str) -> None:
        """A session needs the voice: pin it and start loading if needed."""
        async with self._lock:
            self._holders.add(owner)
            self._cancel_eviction()
            self._speculative_start = False
            if not self._started:
                blocked = await self._gpu_blocked()
                if blocked:
                    logger.warning("voice model not loaded: %s (fallback voice)", blocked)
                    self.last_reason = blocked
                    return
                if not await self._start(f"session {owner}"):
                    return  # no clone configured: nothing to hold
            self._set(VoiceState.WARM, f"session {owner}")

    async def release(self, owner: str) -> None:
        async with self._lock:
            self._holders.discard(owner)
            if self._holders or not self._started:
                return
            if self.policy.preload == "always":
                self._set(VoiceState.COOLDOWN, "preload=always")
                return
            if self.policy.preload == "on_demand" or self.policy.cooldown_seconds == 0:
                await self._evict_locked("session ended (on_demand)")
                return
            self._set(VoiceState.COOLDOWN, "session ended")
            self._cancel_eviction()
            self._evict_task = asyncio.ensure_future(self._evict_after(self.policy.cooldown_seconds))

    async def check_pressure(self) -> None:
        """Periodic: give the GPU back when another app needs it."""
        async with self._lock:
            if self.state is not VoiceState.COOLDOWN or self.policy.preload == "always":
                return
            busy = set(self.policy.gpu_busy_processes) & await asyncio.to_thread(self._running)
            if busy:
                await self._evict_locked(f"GPU-heavy app running ({', '.join(sorted(busy))})")
                return
            free = await asyncio.to_thread(self._free_vram)
            if self.policy.evict_on_pressure and free is not None and free < self.policy.min_free_vram_mb:
                await self._evict_locked(f"GPU memory pressure ({free:.0f} MiB free)")

    async def shutdown(self) -> None:
        self._cancel_eviction()
        if self._started and self.tts is not None:
            await self.tts.stop()
        self._started = False
        self._set(VoiceState.COLD, "shutdown")

    # -- internals ------------------------------------------------------------
    def _cancel_eviction(self) -> None:
        if self._evict_task is not None and not self._evict_task.done():
            self._evict_task.cancel()
        self._evict_task = None

    async def _evict_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        while True:
            async with self._lock:
                if self.state is not VoiceState.COOLDOWN or await self._evict_locked("cooldown elapsed"):
                    return
            await asyncio.sleep(2.0)  # synthesis in flight: try again shortly

    async def _evict_locked(self, reason: str) -> bool:
        if self._holders or not self._started:
            return False
        if getattr(self.tts, "busy", False):
            logger.info("eviction deferred: synthesis in progress")
            return False
        await self.tts.stop()
        self._started = False
        self._speculative_start = False
        self._set(VoiceState.COLD, reason)
        hub.publish("voice.evicted", reason=reason)
        return True
