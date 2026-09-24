"""Provider-neutral runtime ports and health contracts."""

from __future__ import annotations

from dataclasses import dataclass
import enum
from typing import Any, AsyncIterator, Mapping, Protocol, TypeAlias


# Re-exported at runtime so adapters can import TurnContext from contracts
# without a circular import (turn imports config only under TYPE_CHECKING).
from .turn import TurnContext


class HealthStatus(str, enum.Enum):
    """Health of a component."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class HealthReport:
    """Health of one component; ``required`` components must be healthy for the runtime to be ready."""
    name: str
    status: HealthStatus
    detail: str = ""
    required: bool = True
    retryable: bool = False


@dataclass(frozen=True)
class Transcript:
    """Recognized speech."""
    text: str
    is_final: bool = False


@dataclass(frozen=True)
class AgentToken:
    """A streamed text token from an agent."""
    text: str


@dataclass(frozen=True)
class AgentToolRequest:
    """An agent asking the gateway to run a tool."""
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class AgentStatus:
    """A progress note from an agent."""
    detail: str


AgentEvent: TypeAlias = AgentToken | AgentToolRequest | AgentStatus


class AudioCapture(Protocol):
    """Port: microphone audio for a turn."""

    def capture(self, context: TurnContext) -> AsyncIterator[bytes]: ...


class AudioPlayer(Protocol):
    """Port: plays synthesized audio."""

    async def play(self, audio: AsyncIterator[bytes], context: TurnContext) -> None: ...


class VoiceActivityDetector(Protocol):
    """Port: decides whether a frame contains speech."""

    def is_speech(self, audio: bytes) -> bool: ...


class WakeDetector(Protocol):
    """Port: detects the wake word in a frame."""

    def detected(self, audio: bytes) -> bool: ...


class SpeechToText(Protocol):
    """Port: transcribes captured audio."""

    def transcribe(self, audio: AsyncIterator[bytes], context: TurnContext) -> AsyncIterator[Transcript]: ...


class TextToSpeech(Protocol):
    """Port: synthesizes text to audio chunks."""

    def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]: ...


class IntentClassifier(Protocol):
    """Port: routes an utterance to a fast command, the model or Hermes."""

    async def classify(self, text: str, context: TurnContext) -> str: ...


class AgentRuntime(Protocol):
    """Port: multi-step agent that streams tokens, tool requests and status."""

    def respond(self, text: str, context: TurnContext) -> AsyncIterator[AgentEvent]: ...


class MemoryProvider(Protocol):
    """Port: recalls memories relevant to a query."""

    async def recall(self, query: str, context: TurnContext) -> list[str]: ...


class ModelProvider(Protocol):
    """Port: streams a model reply for a prompt."""

    def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]: ...


class Tool(Protocol):
    """Port: an executable tool registered with the gateway."""
    name: str

    async def execute(self, arguments: dict[str, Any], context: TurnContext) -> Any: ...


class ManagedComponent(Protocol):
    """Port: a component the supervisor starts, health-checks and stops."""
    name: str
    required: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def health(self) -> HealthReport: ...
