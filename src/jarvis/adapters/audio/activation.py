"""Configurable activation model (wake word, push-to-talk, manual, continuous)."""

from __future__ import annotations

import enum
import re
import time
import unicodedata


def _normalize_word(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


def _levenshtein(a: str, b: str) -> int:
    previous_row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current_row = [i]
        for j, char_b in enumerate(b, start=1):
            current_row.append(
                min(
                    current_row[j - 1] + 1,
                    previous_row[j] + 1,
                    previous_row[j - 1] + (char_a != char_b),
                )
            )
        previous_row = current_row
    return previous_row[-1]


# ponytail: fuzzy wake-word ceiling is a single-character edit on words within
# one character of the wake word's length — enough to absorb small faster-whisper
# slips ("carvis" for "jarvis") without matching unrelated similar-length words
# ("Javi"). Heavier garbling still requires the user to repeat. Upgrade path if
# this proves insufficient: phonetic matching (e.g. Soundex tuned for Spanish)
# or a larger STT model.
_MAX_WAKE_WORD_EDIT_DISTANCE = 1

# One of these may precede the wake word; anything else before it rejects.
# Whisper often writes a spoken "hey" as "y"/"e"/"i" (and "Ake"/"hay" on this mic).
_WAKE_GREETINGS = frozenset({"hey", "ey", "hei", "ei", "y", "e", "i", "oye", "eh", "ok", "okay", "hola", "ake", "eik", "hay"})


def _fuzzy_matches_wake_word(word: str, wake_word: str) -> bool:
    if word == wake_word:
        return True
    if abs(len(word) - len(wake_word)) > 1:
        return False
    return _levenshtein(word, wake_word) <= _MAX_WAKE_WORD_EDIT_DISTANCE


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
        if match is None:
            return None
        if _normalize_word(match.group(1)) in _WAKE_GREETINGS:
            # "¡Hey, Jarvis! ..." — people naturally lead with a greeting.
            match = re.match(r"^\W*(\w+)(.*)$", match.group(2), flags=re.DOTALL)
            if match is None:
                return None
        candidate = _normalize_word(match.group(1))
        if not _fuzzy_matches_wake_word(candidate, _normalize_word(self.wake_word)):
            return None
        return match.group(2).lstrip(" \t,.:;!?¿¡-")

    def matches_wake_word(self, text: str) -> bool:
        return self.command_after_wake_word(text) is not None
