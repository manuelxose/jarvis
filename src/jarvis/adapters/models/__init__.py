"""Model provider adapters (OpenAI-compatible, local Ollama) and fallback."""

from .fallback import ProviderChain

__all__ = ["ProviderChain"]
