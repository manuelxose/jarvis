"""Real-hardware voice acceptance — thin wrapper around the v2 diagnostic.

Run from the repository root on Windows:

    .\\.venv\\Scripts\\python.exe .\\diagnostico_voice_acceptance.py [config.json]

Prefer the supported CLI command when the package is installed:

    .\\.venv\\Scripts\\jarvis.exe diagnose voice --acceptance

Both paths use only the ``src/jarvis/`` production adapters (``MicCapture``
WASAPI capture and the configured STT provider). They never import legacy
``main.py`` / ``voice/*`` / ``brain/*`` wiring and never use fakes, generated
audio, or a mocked WASAPI/STT provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from jarvis.application.voice_acceptance import format_report, run_voice_acceptance  # noqa: E402
from jarvis.config import load_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = Path(args[0]) if args else ROOT / "config.json"
    config = load_config(config_path)
    report = run_voice_acceptance(config)
    print("\n== JSON report ==", flush=True)
    print(format_report(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
