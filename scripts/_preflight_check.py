"""Bounded, secret-free preflight for the S05-T03 laptop acceptance run.

Prints only: merged provider order, whether each provider's API key is
present (boolean only), remaining daily budget, clone worker profile/mode
from config, and nvidia-smi VRAM. Never prints a key value. Read-only: does
not mutate config, credentials, or personal audio.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import load_config  # noqa: E402


def summarize_balance(payload: dict) -> str:
    """Pure formatting of a DeepSeek /user/balance response -- no network, so
    it is unit-testable without a live account."""
    is_available = payload.get("is_available")
    infos = payload.get("balance_infos") or []
    first = infos[0] if infos else {}
    total_balance = first.get("total_balance")
    currency = first.get("currency")
    return f"deepseek_balance: is_available={is_available} total_balance={total_balance} currency={currency}"


def _deepseek_balance_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base + "/user/balance"


def probe_deepseek_balance(base_url: str, api_key: Optional[str], timeout: float = 10.0) -> str:
    """GETs the DeepSeek balance endpoint with stdlib urllib only. Never
    prints the key or request headers -- only the parsed, secret-free
    summary or a bounded failure label naming the exception class/status."""
    url = _deepseek_balance_url(base_url)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key or ''}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return summarize_balance(payload)
    except urllib.error.HTTPError as exc:
        return f"deepseek_balance: probe_failed={exc.code}"
    except Exception as exc:  # noqa: BLE001 - diagnostic only, never raises
        return f"deepseek_balance: probe_failed={type(exc).__name__}"


def main() -> int:
    cfg = load_config("config.win.json")
    providers = list(getattr(cfg.models, "providers", []))
    print("provider_order:", [p.name for p in providers])
    for p in providers:
        key = getattr(p, "api_key", None)
        print(f"  provider={p.name} key_present={bool(key)}")
    max_daily = getattr(cfg.models, "max_daily_usd", None)
    print("max_daily_usd:", max_daily)
    print("tts_profile:", getattr(cfg.tts, "profile", None))

    for p in providers:
        if getattr(p, "kind", "openai_compat") == "openai_compat" and "deepseek" in (p.base_url or "").lower():
            print(probe_deepseek_balance(p.base_url, getattr(p, "api_key", None)))

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        )
        print("nvidia_smi_vram:", out.stdout.strip() or out.stderr.strip())
    except Exception as exc:  # pragma: no cover - diagnostic only
        print("nvidia_smi_vram: unavailable (%s)" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
