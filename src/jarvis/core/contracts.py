"""Provider-neutral runtime ports and health contracts."""

from __future__ import annotations

from dataclasses import dataclass
import enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Mapping, Protocol, TypeAlias


if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig

# Re-exported at runtime so adapters can import TurnContext from contracts
# without a circular import (turn imports config only under TYPE_CHECKING).
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


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool = False


@dataclass(frozen=True)
class AgentToken:
    text: str


@dataclass(frozen=True)
class AgentToolRequest:
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class AgentStatus:
    detail: str


AgentEvent: TypeAlias = AgentToken | AgentToolRequest | AgentStatus


class AudioCapture(Protocol):
    def capture(self, context: TurnContext) -> AsyncIterator[bytes]: ...


class AudioPlayer(Protocol):
    async def play(self, audio: AsyncIterator[bytes], context: TurnContext) -> None: ...


class VoiceActivityDetector(Protocol):
    def is_speech(self, audio: bytes) -> bool: ...


class WakeDetector(Protocol):
    def detected(self, audio: bytes) -> bool: ...


class SpeechToText(Protocol):
    def transcribe(self, audio: AsyncIterator[bytes], context: TurnContext) -> AsyncIterator[Transcript]: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]: ...


class IntentClassifier(Protocol):
    async def classify(self, text: str, context: TurnContext) -> str: ...


class AgentRuntime(Protocol):
    def respond(self, text: str, context: TurnContext) -> AsyncIterator[AgentEvent]: ...


class MemoryProvider(Protocol):
    async def recall(self, query: str, context: TurnContext) -> list[str]: ...


class ModelProvider(Protocol):
    def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]: ...


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
