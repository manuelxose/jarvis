# Legacy Jarvis v1 runtime (reference only)

This directory contains the **pre-v2** runtime and is **not part of production
execution**. It is preserved only as reference material for migration and for
the legacy-specific tests under `tests/`.

## What lives here

- `main.py` — the old composition root (replaced by `src/jarvis/apps/cli.py`).
- `brain/` — legacy LLM/routing/prompt/memory modules.
- `voice/` — legacy audio/STT/TTS/wake-word modules.
- `actions/` — legacy AIS/trading/PC-control/web-search actions.
- `cache/` — legacy audio cache.
- `config.yaml` — legacy YAML configuration (v2 uses `config.json`).
- `legacy_setup.py`, `bootstrap` helpers, `diagnostico_*.py` — legacy provisioning
  and hardware diagnostics.

## Boundary guarantee

The v2 runtime under `src/jarvis/` does **not** import anything from `legacy/`.
The single production composition root is `jarvis.apps.cli:main`, invoked as:

```text
run_jarvis.bat
run_jarvis.ps1
python -m jarvis
jarvis run
```
