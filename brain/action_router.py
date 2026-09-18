from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from actions.ais_monitor import AISMonitor
from actions.pc_control import PCController
from actions.trading_monitor import TradingMonitor
from actions.web_search import WebSearch
from brain.llm import OllamaClient


LOGGER = logging.getLogger(__name__)

def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    return " ".join(cleaned.split())


class ActionRouter:
    """Intent classifier plus action dispatcher."""

    def __init__(
        self,
        llm_client: OllamaClient | None,
        pc_control: PCController | None = None,
        ais_monitor: AISMonitor | None = None,
        trading_monitor: TradingMonitor | None = None,
        web_search: WebSearch | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.pc_control = pc_control
        self.ais_monitor = ais_monitor
        self.trading_monitor = trading_monitor
        self.web_search = web_search

    def classify_intent(self, text: str) -> str:
        lower = _normalize_text(text)

        pc_keywords = [
            "abre ",
            "abrir ",
            "abreme ",
            "cierra ",
            "cerrar ",
            "ejecuta ",
            "ejecutar ",
            "captura",
            "explorador",
            "estado del sistema",
            "bloc de notas",
            "blog de notas",
            "notepad",
            "editor de texto",
        ]
        ais_keywords = ["barcos", "ais", "puerto", "trafico maritimo", "senal ais"]
        trading_keywords = ["trading", "oro", "xau", "mt4", "mt5", "portfolio", "cartera"]
        web_keywords = ["busca en internet", "busca web", "investiga", "busca en la web"]

        if any(keyword in lower for keyword in pc_keywords):
            return "PC_CONTROL"
        if any(keyword in lower for keyword in ais_keywords):
            return "AIS_MONITOR"
        if any(keyword in lower for keyword in trading_keywords):
            return "TRADING"
        if any(keyword in lower for keyword in web_keywords):
            return "WEB_SEARCH"

        # Conversation is the common path. A remote intent-classification call
        # followed by a second answer call doubles latency for every chat turn.
        return "CONVERSATION"

    def route(self, text: str) -> dict[str, Any]:
        intent = self.classify_intent(text)

        try:
            if intent == "PC_CONTROL":
                return {"intent": intent, "handled": True, "response": self._handle_pc_control(text)}
            if intent == "AIS_MONITOR":
                return {"intent": intent, "handled": True, "response": self._handle_ais(text)}
            if intent == "TRADING":
                return {"intent": intent, "handled": True, "response": self._handle_trading(text)}
            if intent == "WEB_SEARCH":
                return {"intent": intent, "handled": True, "response": self._handle_web_search(text)}
        except Exception as exc:
            LOGGER.exception("Action execution failed: %s", exc)
            return {
                "intent": intent,
                "handled": True,
                "response": "No he podido completar esa accion.",
            }

        return {"intent": "CONVERSATION", "handled": False, "response": None}

    def _handle_pc_control(self, text: str) -> str:
        if not self.pc_control:
            return "El modulo de control del PC no esta disponible."

        lower = _normalize_text(text)

        open_match = re.search(r"\b(?:abre|abrir|abreme)\s+(?:el|la|los|las)?\s*(.+)$", lower)
        if open_match:
            target = open_match.group(1).strip()
            ok = self.pc_control.open_application(target)
            return "Comando ejecutado." if ok else "No pude abrir la aplicacion solicitada."

        close_match = re.search(r"\b(?:cierra|cerrar)\s+(?:el|la|los|las)?\s*(.+)$", lower)
        if close_match:
            target = close_match.group(1).strip()
            ok = self.pc_control.close_application(target)
            return "Comando ejecutado." if ok else "No pude cerrar la aplicacion solicitada."

        command_match = re.search(r"\b(?:ejecuta|ejecutar)\s+(.+)$", text, re.IGNORECASE)
        if command_match:
            command = command_match.group(1).strip()
            output = self.pc_control.run_command(command)
            return f"He ejecutado el comando. Resultado: {output}"

        if "captura" in lower:
            screenshot_path = self.pc_control.take_screenshot()
            return f"Captura tomada. Archivo guardado en {screenshot_path}"

        explorer_match = re.search(r"explorador\s+(.+)$", text, re.IGNORECASE)
        if explorer_match:
            path = explorer_match.group(1).strip()
            ok = self.pc_control.open_in_explorer(path)
            return "Comando ejecutado." if ok else "No pude abrir esa ruta en el explorador."

        if "estado del sistema" in lower:
            status = self.pc_control.get_system_status()
            return (
                f"CPU al {status['cpu_percent']:.1f} por ciento, "
                f"RAM al {status['ram_percent']:.1f} por ciento, "
                f"disco libre {status['disk_free_gb']:.1f} gigas."
            )

        # Common voice pattern: saying only app name after wake phrase.
        candidate = re.sub(r"\bpor favor\b", "", lower).strip()
        candidate = re.sub(r"^(?:el|la|los|las)\s+", "", candidate).strip()
        if candidate in self.pc_control.app_map:
            ok = self.pc_control.open_application(candidate)
            return "Comando ejecutado." if ok else "No pude abrir la aplicacion solicitada."

        return "No reconoci una accion de control del PC concreta."

    def _handle_ais(self, text: str) -> str:
        if not self.ais_monitor:
            return "El modulo AIS no esta disponible."

        lower = text.lower()
        if "estado" in lower or "senal" in lower:
            status = self.ais_monitor.get_ais_status()
            if status.get("activo"):
                return (
                    "El receptor AIS esta activo. "
                    f"Senales en la ultima hora: {status.get('senales_recibidas_ultima_hora', 0)}."
                )
            return f"El receptor AIS no esta activo. {status.get('error') or ''}".strip()

        vessels = self.ais_monitor.get_nearby_vessels()
        return self.ais_monitor.format_vessel_report(vessels)

    def _handle_trading(self, text: str) -> str:
        if not self.trading_monitor:
            return "El modulo de trading no esta disponible."

        lower = text.lower()
        if "oro" in lower or "xau" in lower:
            gold_data = self.trading_monitor.get_gold_price()
            if not gold_data.get("available"):
                return gold_data.get("error", "No pude consultar el precio del oro.")
            price = gold_data.get("price")
            if isinstance(price, (float, int)):
                return f"El oro cotiza en {price:.2f} dolares por onza."
            return "No pude leer un precio valido para el oro."

        portfolio = self.trading_monitor.get_portfolio_status()
        return self.trading_monitor.format_trading_report(portfolio)

    def _handle_web_search(self, text: str) -> str:
        if not self.web_search:
            return "El modulo de busqueda no esta disponible."

        query = re.sub(r"^(busca en internet|busca web|busca en la web|investiga)\s*", "", text, flags=re.I)
        query = query.strip() or text.strip()
        results = self.web_search.search(query, max_results=5)
        return self.web_search.format_search_results(results)
