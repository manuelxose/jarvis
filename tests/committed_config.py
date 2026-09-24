"""Load the versioned config.json without the developer's config.local.json."""

import shutil
import tempfile
from pathlib import Path

from jarvis.config import RuntimeConfig, load_config

_ROOT = Path(__file__).resolve().parents[1]


def load_committed_config(name: str = "config.json") -> RuntimeConfig:
    with tempfile.TemporaryDirectory() as tmp:
        return load_config(shutil.copy(_ROOT / name, tmp), {})
