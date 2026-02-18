from __future__ import annotations

import logging
import re
from collections import deque
from pathlib import Path
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)


class TradingMonitor:
    """Trading monitor for MT4/MT5 logs plus basic gold price lookup."""

    def __init__(
        self,
        mt4_log_path: str | Path,
        gold_api_url: str = "https://api.metals.live/v1/spot/gold",
    ) -> None:
        self.mt4_log_path = Path(mt4_log_path)
        self.gold_api_url = gold_api_url

    def _tail_lines(self, file_path: Path, max_lines: int = 500) -> list[str]:
        lines: deque[str] = deque(maxlen=max_lines)
        with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                lines.append(line.rstrip())
        return list(lines)

    def _extract_first_float(self, pattern: str, lines: list[str]) -> float | None:
        regex = re.compile(pattern, re.IGNORECASE)
        for line in reversed(lines):
            match = regex.search(line)
            if match:
                try:
                    return float(match.group(1))
                except Exception:
                    continue
        return None

    def _extract_first_int(self, pattern: str, lines: list[str]) -> int | None:
        regex = re.compile(pattern, re.IGNORECASE)
        for line in reversed(lines):
            match = regex.search(line)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    continue
        return None

    def get_portfolio_status(self) -> dict[str, Any]:
        if not self.mt4_log_path.exists():
            return {
                "available": False,
                "error": f"Ruta de logs no encontrada: {self.mt4_log_path}",
            }

        log_files = sorted(
            self.mt4_log_path.rglob("*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not log_files:
            return {"available": False, "error": "No se encontraron logs de MT4/MT5."}

        latest_log = log_files[0]
        lines = self._tail_lines(latest_log, max_lines=800)
        balance = self._extract_first_float(r"balance[:=]\s*([-+]?\d+(?:\.\d+)?)", lines)
        equity = self._extract_first_float(r"equity[:=]\s*([-+]?\d+(?:\.\d+)?)", lines)
        positions = self._extract_first_int(r"(?:open positions|posiciones abiertas)[:=]\s*(\d+)", lines)
        pnl = self._extract_first_float(r"(?:p\/?l(?: del dia)?|profit)[:=]\s*([-+]?\d+(?:\.\d+)?)", lines)

        return {
            "available": True,
            "source_log": str(latest_log),
            "balance": balance,
            "equity": equity,
            "open_positions": positions,
            "daily_pnl": pnl,
        }

    def get_gold_price(self) -> dict[str, Any]:
        try:
            response = requests.get(self.gold_api_url, timeout=6)
            response.raise_for_status()
            payload = response.json()

            # metals.live often returns [[timestamp, price]].
            if isinstance(payload, list) and payload and isinstance(payload[0], list) and len(payload[0]) >= 2:
                latest = payload[0]
                return {
                    "available": True,
                    "price": float(latest[1]),
                    "timestamp": latest[0],
                    "change_24h": None,
                    "trend": "unknown",
                }

            if isinstance(payload, dict):
                return {
                    "available": True,
                    "price": payload.get("price"),
                    "timestamp": payload.get("timestamp"),
                    "change_24h": payload.get("change_24h"),
                    "trend": payload.get("trend", "unknown"),
                }

            return {"available": False, "error": "Formato inesperado en API de oro."}
        except Exception as exc:
            return {"available": False, "error": f"No se pudo obtener precio del oro: {exc}"}

    def format_trading_report(self, data: dict[str, Any]) -> str:
        if not data.get("available", False):
            return data.get("error", "No hay datos de trading disponibles.")

        balance = data.get("balance")
        equity = data.get("equity")
        positions = data.get("open_positions")
        pnl = data.get("daily_pnl")

        parts = ["Estado de trading:"]
        if isinstance(balance, (float, int)):
            parts.append(f"balance {balance:.2f}")
        if isinstance(equity, (float, int)):
            parts.append(f"equity {equity:.2f}")
        if isinstance(positions, int):
            parts.append(f"posiciones abiertas {positions}")
        if isinstance(pnl, (float, int)):
            parts.append(f"P y L del dia {pnl:.2f}")

        if len(parts) == 1:
            return "Hay logs de trading, pero no pude extraer metricas claras."
        return ", ".join(parts) + "."

