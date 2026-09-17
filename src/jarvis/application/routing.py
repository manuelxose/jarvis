"""Explicit, inspectable request routing for the Jarvis turn pipeline.

The router is the single decision point that maps an incoming utterance to one
of three paths::

    fast_command  -> deterministic local tool (no model, no Hermes)
    fast_model    -> streaming conversational model
    hermes        -> multi-step agentic execution

The classifier is deliberately small and conservative: deterministic patterns
cover the low-latency command surface, and anything it is not confident about
routes upward rather than guessing.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

from jarvis.core.contracts import IntentClassifier, TurnContext

logger = logging.getLogger(__name__)


# URLs are matched against the raw text: normalize strips the `://` scheme.
_URL_COMMAND = re.compile(
    r"\b(?:abre|abrir|open|go to|navega a)\s+(https?://\S+|www\.\S+\.\S+)",
    re.IGNORECASE,
)


# File paths are matched against the raw text for the same reason: normalize
# folds punctuation and would turn "notas.txt" into "notas txt", losing the
# extension. The path is captured verbatim so the file tool can resolve a real
# filesystem path. Matched before open_application so "abre el archivo X"
# resolves to the file tool (first match wins).
_FILE_COMMAND = re.compile(
    r"\b(?:lee|leeme|leer|abre)\s+(?:el\s+)?(?:archivo|fichero)\s+(.+)",
    re.IGNORECASE,
)


# Ordered (pattern, tool, argument extractor, confidence[, defaults]). First match
# wins. The optional 5th element is a constant-arguments dict merged into the
# match before the captured group, so `action` can be injected deterministically.
_COMMAND_PATTERNS: tuple[tuple, ...] = (
    # clipboard read / copy (read-path only; file write stays Hermes-only, D012)
    (
        re.compile(
            r"\b(?:que\s+hay\s+en\s+el\s+portapapeles|lee(?:me)?\s+el\s+portapapeles|pega|paste)\b"
        ),
        "clipboard",
        "",
        0.95,
        {"action": "read"},
    ),
    (
        re.compile(r"\bcopia\s+(.+?)\s+(?:en|al)\s+(?:el\s+)?portapapeles\b"),
        "clipboard",
        "content",
        0.9,
        {"action": "copy"},
    ),
    # open application
    (
        re.compile(r"\b(?:abre|abrir|abreme|open|launch|start)\s+(?:la\s+)?(?:aplicacion\s+)?(.+)"),
        "open_application",
        "application",
        0.92,
    ),
    # volume up
    (re.compile(r"\b(?:sube|subir|aumenta|más alto|mas alto)\s+(?:el\s+)?(?:volumen)\b"), "volume_up", "", 0.95),
    (re.compile(r"\bvolume\s+up\b|\b(?:sube|aumenta)\s+(?:un\s+poco\s+)?el\s+volumen"), "volume_up", "", 0.95),
    (re.compile(r"\bsube\s+el\s+volumen\b"), "volume_up", "", 0.97),
    # volume down
    (re.compile(r"\b(?:baja|bajar|disminuye)\s+(?:el\s+)?(?:volumen)\b"), "volume_down", "", 0.95),
    (re.compile(r"\bvolume\s+down\b|\b(?:baja|disminuye)\s+el\s+volumen"), "volume_down", "", 0.95),
    (re.compile(r"\bbaja\s+el\s+volumen\b"), "volume_down", "", 0.97),
    # set volume to a number
    (
        re.compile(r"\b(?:pon|poner|set)\s+(?:el\s+)?(?:volumen)\s+(?:al|a|en)?\s*(\d{1,3})\b"),
        "volume_set",
        "level",
        0.93,
    ),
    (re.compile(r"\bvolumen\s+(?:al|a)\s+(\d{1,3})\b"), "volume_set", "level", 0.95),
    # mute / unmute
    (re.compile(r"\b(?:silencia|silencio|mute|mutea)\b"), "mute", "", 0.95),
    (re.compile(r"\b(?:activar|quitar)\s+(?:el\s+)?(?:sonido|silencioso)|(?:unmute|restaurar\s+sonido)\b"), "unmute", "", 0.9),
    # media controls
    (re.compile(r"\b(?:pausa|pausar|pause)\b"), "media_play_pause", "", 0.9),
    (re.compile(r"\b(?:reanuda|reanudar|reproduce|play|continua)\b"), "media_play_pause", "", 0.85),
    (re.compile(r"\b(?:siguiente|next|siguiente\s+cancion)\b"), "media_next", "", 0.92),
    (re.compile(r"\b(?:anterior|previous|anterior\s+cancion)\b"), "media_previous", "", 0.92),
    # time / date
    (re.compile(r"\b(?:que\s+)?hora\s+es\b|\bwhat\s+time\s+is\s+it\b"), "time", "", 0.96),
    (re.compile(r"\b(?:que\s+)?(?:dia|fecha)\s+(?:es|de\s+hoy)\b|\bwhat(?:'s| is)?\s+the\s+date\b"), "date", "", 0.96),
    # system state
    (re.compile(r"\b(?:estado\s+del\s+sistema|system\s+status|system\s+info)\b"), "system_info", "", 0.93),
    # stop / cancel / repeat
    (re.compile(r"^\s*(?:para|detente|stop|cancel|cancela)\s*$"), "stop", "", 0.97),
    (re.compile(r"^\s*(?:repite|repite\s+eso|repeat)\s*$"), "repeat", "", 0.95),
)


@dataclass(frozen=True)
class FastCommandMatch:
    """A deterministic command extracted from an utterance."""

    name: str
    arguments: Mapping[str, str]
    confidence: float
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    """An inspectable routing outcome."""

    route: str
    confidence: float
    reason: str
    command: FastCommandMatch | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "route": self.route,
            "confidence": self.confidence,
            "reason": self.reason,
        }
        if self.command is not None:
            result["command"] = {
                "name": self.command.name,
                "arguments": dict(self.command.arguments),
            }
        return result


def normalize(text: str) -> str:
    """Fold accents, punctuation and case for stable matching."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\s]", " ", without_accents.lower()).strip()


class FastCommandClassifier:
    """Deterministic, conservative fast-command matcher."""

    def match(self, text: str) -> FastCommandMatch | None:
        raw = (text or "").strip()
        url = _URL_COMMAND.search(raw)
        if url is not None:
            return FastCommandMatch(
                name="open_url",
                arguments={"url": url.group(1)},
                confidence=0.95,
                reason="matched deterministic pattern for open_url",
            )
        file_match = _FILE_COMMAND.search(raw)
        if file_match is not None:
            return FastCommandMatch(
                name="file",
                arguments={"action": "read", "path": file_match.group(1).strip()},
                confidence=0.92,
                reason="matched deterministic pattern for file",
            )
        normalized = normalize(raw)
        if not normalized:
            return None
        for pattern, tool, argument_key, confidence, *rest in _COMMAND_PATTERNS:
            match = pattern.search(normalized)
            if match is None:
                continue
            arguments: dict[str, str] = dict(rest[0]) if rest else {}
            if argument_key:
                arguments[argument_key] = (match.group(1) or "").strip() if match.groups() else ""
            return FastCommandMatch(
                name=tool,
                arguments=arguments,
                confidence=confidence,
                reason=f"matched deterministic pattern for {tool}",
            )
        return None


class Router:
    """Route an utterance to fast command, fast model, or Hermes."""

    AGENT_CUES = (
        "planifica", "planificar", "investiga", "investigar", "multipaso", "multistep",
        "autonomo", "tarea larga", "orquesta", "research", "plan ",
        "haz una tarea", "pasos", "ejecuta la tarea", "workflow",
    )

    def __init__(
        self,
        classifier: FastCommandClassifier | None = None,
        intent_classifier: IntentClassifier | None = None,
    ) -> None:
        self._classifier = classifier or FastCommandClassifier()
        self._intent_classifier = intent_classifier

    async def route(self, text: str, context: TurnContext) -> RouteDecision:
        command = self._classifier.match(text)
        if command is not None:
            return RouteDecision(
                route="fast_command",
                confidence=command.confidence,
                reason=command.reason,
                command=command,
            )

        if self._intent_classifier is not None:
            try:
                intent = await self._intent_classifier.classify(text, context)
            except Exception:
                logger.warning(
                    "intent classifier failed; falling back to fast_model", exc_info=True
                )
                intent = "fast_model"
            if intent in {"hermes", "agent"}:
                return RouteDecision(
                    route="hermes",
                    confidence=0.8,
                    reason="intent classifier signalled agentic multi-step request",
                )

        normalized = normalize(text)
        if any(cue in normalized for cue in self.AGENT_CUES):
            return RouteDecision(
                route="hermes",
                confidence=0.75,
                reason="agentic multi-step cue detected",
            )

        return RouteDecision(
            route="fast_model",
            confidence=0.7,
            reason="conversational request; no deterministic command matched",
        )
