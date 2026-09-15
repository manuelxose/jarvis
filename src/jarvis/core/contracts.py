"""Provider-neutral runtime ports and health contracts."""

from __future__ import annotations

from dataclasses import dataclass
import enum
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig
    from .turn import TurnContext


class HealthStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class HealthReport:
    name: str
    status: HealthStatus
    detail: str = ""
    required: bool = True
    retryable: bool = False


class AudioCapture(Protocol):
    async def capture(self, context: TurnContext) -> bytes: ...


class AudioPlayer(Protocol):
    async def play(self, audio: bytes, context: TurnContext) -> None: ...


class VoiceActivityDetector(Protocol):
    def is_speech(self, audio: bytes) -> bool: ...


class WakeDetector(Protocol):
    def detected(self, audio: bytes) -> bool: ...


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, context: TurnContext) -> str: ...


class TextToSpeech(Protocol):
    async def synthesize(self, text: str, context: TurnContext) -> bytes: ...


class IntentClassifier(Protocol):
    async def classify(self, text: str, context: TurnContext) -> str: ...


class AgentRuntime(Protocol):
    async def respond(self, text: str, context: TurnContext) -> str: ...


class MemoryProvider(Protocol):
    async def recall(self, query: str, context: TurnContext) -> list[str]: ...


class ModelProvider(Protocol):
    async def generate(self, prompt: str, context: TurnContext) -> str: ...


class Tool(Protocol):
    name: str

    async def execute(self, arguments: dict[str, Any], context: TurnContext) -> Any: ...


class Skill(Protocol):
    name: str

    async def handle(self, text: str, context: TurnContext) -> str: ...


class HealthCheck(Protocol):
    async def health(self) -> HealthReport: ...


class StartupEffect(Protocol):
    async def apply(self, config: RuntimeConfig) -> None: ...


class ManagedComponent(Protocol):
    name: str
    required: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def health(self) -> HealthReport: ...
