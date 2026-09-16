"""Configurable activation model (wake word, push-to-talk, manual, continuous)."""

from __future__ import annotations

import enum
import time


class ActivationMode(str, enum.Enum):
    WAKE_WORD = "wake_word"
    PUSH_TO_TALK = "push_to_talk"
    MANUAL = "manual"
    CONTINUOUS = "continuous"


class ConversationWindow:
    """Tracks whether the follow-up window is open after a recent turn."""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
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
        self.mode = ActivationMode(mode if mode in {m.value for m in ActivationMode} else "wake_word")
        self.wake_word = wake_word.lower()
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

    def matches_wake_word(self, text: str) -> bool:
        import re
        import unicodedata

        normalized = unicodedata.normalize("NFKD", text or "")
        without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        tokens = re.findall(r"[a-z0-9]+", without_accents.lower())
        return any(self.wake_word in token or token in self.wake_word for token in tokens)
