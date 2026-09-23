"""Headless entry point for the Jarvis sentinel (started by the Startup-folder shortcut)."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)
# pythonw.exe has no console: give print() somewhere harmless to go.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

from jarvis.apps.cli import main  # noqa: E402

sys.exit(main(["daemon", *sys.argv[1:]]))
