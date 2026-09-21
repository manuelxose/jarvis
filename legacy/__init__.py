"""Legacy Jarvis v1 runtime — reference only, not part of production execution.

This package holds the pre-v2 modules (``main.py``, ``brain``, ``voice``,
``actions``, ``cache``) and legacy provisioning/diagnostics. The v2 production
runtime lives under ``src/jarvis`` and is the only composition root. Nothing
under ``src/jarvis`` imports from this package.
"""
