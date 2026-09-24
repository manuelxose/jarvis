"""Typed domain errors for the Jarvis runtime.

The runtime distinguishes *transient* failures (which may be retried or
fallback-ed across providers) from *configuration* and *application* failures
(which must fail fast and never be blindly retried).
"""

from __future__ import annotations


class JarvisError(Exception):
    """Base class for all Jarvis domain errors."""


class ProviderError(JarvisError):
    """A provider failed to produce a usable result.

    ``transient`` selects retry/fallback behaviour. Transient errors (quota,
    rate limit, temporary outage, network blips) are safe to retry with bounded
    backoff; configuration and request-shape errors are not.
    """

    def __init__(self, message: str, *, transient: bool = True, provider: str | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.provider = provider


class ProviderUnavailable(ProviderError):
    """A provider is temporarily unreachable or unhealthy."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, transient=True, provider=provider)


class ProviderConfigError(ProviderError):
    """A provider is misconfigured (missing key, bad URL, invalid model)."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, transient=False, provider=provider)


class ToolError(JarvisError):
    """A tool failed to execute."""


class ToolNotFoundError(ToolError):
    """The requested tool is not registered or is not allowlisted."""


class ToolPermissionDenied(ToolError):
    """A tool action requires confirmation or a higher permission class."""


class ToolExecutionError(ToolError):
    """A tool raised while executing."""


class HermesError(JarvisError):
    """Hermes child process failed, timed out, or returned malformed data."""


class MemoryError(JarvisError):
    """The memory store failed or refused an unsafe operation."""
