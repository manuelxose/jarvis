"""Configurable activation model (wake word, push-to-talk, manual, continuous)."""

from __future__ import annotations

import enum
import re
import time
import unicodedata


def _normalize_word(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


class ActivationMode(str, enum.Enum):
    WAKE_WORD = "wake_word"
    PUSH_TO_TALK = "push_to_talk"
    MANUAL = "manual"
    CONTINUOUS = "continuous"


class ConversationWindow:
    """Tracks whether the follow-up window is open after a recent turn."""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        self.timeout_seconds = timeout_seconds
        self._last_activity = 0.0

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()

    @property
    def active(self) -> bool:
        return (time.monotonic() - self._last_activity) <= self.timeout_seconds

    def reset(self) -> None:
        self._last_activity = 0.0


class ActivationManager:
    """Decide whether the wake word is required for the next utterance."""

    def __init__(
        self,
        *,
        mode: str = "wake_word",
        wake_word: str = "jarvis",
        cooldown_seconds: float = 1.2,
        conversation_timeout_seconds: float = 8.0,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        normalized_wake_word = wake_word.strip().lower()
        if not normalized_wake_word:
            raise ValueError("wake_word must not be empty")
        self.mode = ActivationMode(mode if mode in {m.value for m in ActivationMode} else "wake_word")
        self.wake_word = normalized_wake_word
        self.cooldown_seconds = cooldown_seconds
        self._window = ConversationWindow(conversation_timeout_seconds)
        self._last_activation = 0.0

    @property
    def window(self) -> ConversationWindow:
        return self._window

    def note_activation(self) -> None:
        self._last_activation = time.monotonic()

    def note_turn_complete(self) -> None:
        self._window.note_activity()

    def wake_word_required(self) -> bool:
        """True when the user must say the wake word before the next utterance."""
        if self.mode in (ActivationMode.MANUAL, ActivationMode.PUSH_TO_TALK, ActivationMode.CONTINUOUS):
            return False
        if self._window.active:
            return False
        return True

    def in_cooldown(self) -> bool:
        return (time.monotonic() - self._last_activation) < self.cooldown_seconds

    def command_after_wake_word(self, text: str) -> str | None:
        match = re.match(r"^\W*(\w+)(.*)$", text or "", flags=re.DOTALL)
        if match is None or _normalize_word(match.group(1)) != _normalize_word(self.wake_word):
            return None
        return match.group(2).lstrip(" \t,.:;!?¿¡-")

    def matches_wake_word(self, text: str) -> bool:
        return self.command_after_wake_word(text) is not None
