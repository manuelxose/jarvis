"""Versioned, bounded cache of cloned-voice audio (welcomes, fixed acknowledgements).

The key covers everything that changes the sound: the voice profile identity
and version (hash of its files), the exact text, the synthesis settings and the
output format. Entries recorded with another identity are purged when the cache
opens, so re-enrolling the voice can never replay the old voice. Size is bounded
(entries and bytes) with least-recently-used eviction. Callers decide *what* may
be cached: only stable success phrases, never dynamic values or failures.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger("jarvis.voice_cache")

FORMAT = "wav/pcm_s16le/mono"


def profile_version(profile_dir: Path) -> str:
    """Hash of the enrolled profile's files (name, size, mtime); '' when absent."""
    digest = hashlib.sha256()
    found = False
    for name in ("profile.json", "reference.wav", "prompt.pt"):
        path = profile_dir / name
        try:
            stat = path.stat()
        except OSError:
            continue
        found = True
        digest.update(f"{name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
    return digest.hexdigest()[:16] if found else ""


def voice_identity(config: Any) -> dict[str, Any]:
    """Identity of the voice that would synthesize, derived without loading it."""
    from jarvis.adapters.tts.resolve import tts_provider  # noqa: PLC0415
    from jarvis.voice_profile import default_profile_dir  # noqa: PLC0415

    provider = tts_provider(config)
    identity: dict[str, Any] = {"provider": provider, "language": config.tts.language, "format": FORMAT}
    if provider == "qwen_clone":
        profile_dir = Path(config.tts.profile_dir) if config.tts.profile_dir else default_profile_dir()
        name = profile_dir.name
        try:
            name = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8")).get("name", name)
        except (OSError, ValueError):
            pass
        identity.update(profile=name, profile_version=profile_version(profile_dir), model=config.tts.model or "default", chunk_size=config.tts.chunk_size)
    else:
        identity.update(voice=config.tts.voice)
    return identity


class VoiceCache:
    def __init__(self, directory: Path, identity: Mapping[str, Any], *, max_entries: int = 300, max_bytes: int = 64 * 2**20) -> None:
        self.dir = Path(directory)
        self.identity = dict(identity)
        self.identity_hash = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()[:16]
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, Any]] = self._load_index()
        self._purge_other_identities()

    # -- keys ---------------------------------------------------------------
    def key(self, text: str) -> str:
        payload = json.dumps({"identity": self.identity_hash, "text": (text or "").strip()}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    # -- API ----------------------------------------------------------------
    def get(self, text: str) -> Optional[bytes]:
        if not (text or "").strip():
            return None
        key = self.key(text)
        with self._lock:
            entry = self._index.get(key)
            if entry is None:
                return None
            try:
                data = (self.dir / f"{key}.wav").read_bytes()
            except OSError:
                self._index.pop(key, None)
                return None
            entry["last_used"] = time.time()
            self._save_index()
            return data

    def put(self, text: str, data: bytes) -> None:
        text = (text or "").strip()
        if not text or not data:
            return
        key = self.key(text)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{key}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, self.dir / f"{key}.wav")
            self._index[key] = {"text": text, "bytes": len(data), "identity": self.identity_hash, "last_used": time.time()}
            self._evict()
            self._save_index()

    def __contains__(self, text: str) -> bool:
        return self.key(text) in self._index

    def stats(self) -> dict[str, Any]:
        return {"entries": len(self._index), "bytes": sum(e["bytes"] for e in self._index.values()), "identity": self.identity}

    # -- internals ----------------------------------------------------------
    def _load_index(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads((self.dir / "index.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "index.tmp"
            tmp.write_text(json.dumps(self._index, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.dir / "index.json")
        except OSError:
            logger.warning("voice cache index not saved", exc_info=True)

    def _drop(self, key: str) -> None:
        self._index.pop(key, None)
        try:
            (self.dir / f"{key}.wav").unlink()
        except OSError:
            pass

    def _purge_other_identities(self) -> None:
        stale = [k for k, e in self._index.items() if e.get("identity") != self.identity_hash]
        with self._lock:
            for key in stale:
                self._drop(key)
            if stale:
                logger.info("voice cache: dropped %d entries recorded with another voice", len(stale))
                self._save_index()

    def _evict(self) -> None:
        by_age = sorted(self._index, key=lambda k: self._index[k]["last_used"])
        total = sum(e["bytes"] for e in self._index.values())
        while by_age and (len(self._index) > self.max_entries or total > self.max_bytes):
            key = by_age.pop(0)
            total -= self._index[key]["bytes"]
            self._drop(key)
