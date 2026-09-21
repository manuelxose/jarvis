from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import pyautogui


LOGGER = logging.getLogger(__name__)


class PCController:
    """Windows PC control actions with basic command safety."""

    WHITELIST_COMMANDS = {"dir", "ls", "ipconfig", "ping", "netstat", "tasklist", "date", "time"}

    def __init__(self) -> None:
        self.app_map: dict[str, list[str]] = {
            "vs code": ["code"],
            "vscode": ["code"],
            "chrome": ["start", "chrome"],
            "firefox": ["start", "firefox"],
            "terminal": ["start", "wt"],
            "powershell": ["start", "powershell"],
            "explorer": ["explorer"],
            "bloc de notas": ["notepad"],
            "blog de notas": ["notepad"],
            "blog notas": ["notepad"],
            "bloc notas": ["notepad"],
            "editor de texto": ["notepad"],
            "notas": ["notepad"],
            "notepad": ["notepad"],
        }
        self.process_map: dict[str, str] = {
            "chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "vs code": "Code.exe",
            "vscode": "Code.exe",
            "terminal": "WindowsTerminal.exe",
            "notepad": "notepad.exe",
        }
        self.screenshot_dir = Path(__file__).resolve().parents[1] / "cache" / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_name(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text)
        without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        lowered = without_accents.lower()
        cleaned = re.sub(r"[^a-z0-9\\s]+", " ", lowered)
        return " ".join(cleaned.split())

    def open_application(self, name: str) -> bool:
        app_name = self._normalize_name(name)
        command = self.app_map.get(app_name)
        if command is None:
            for alias, alias_cmd in self.app_map.items():
                if alias in app_name or app_name in alias:
                    command = alias_cmd
                    break
        try:
            if command:
                if command[0] == "start":
                    subprocess.Popen(f"start {command[1]}", shell=True)
                else:
                    subprocess.Popen(command)
                return True

            # Fallback: try to open with shell if the name is executable/path.
            os.startfile(name)  # type: ignore[attr-defined]
            return True
        except Exception as exc:
            LOGGER.error("Failed to open application '%s': %s", name, exc)
            return False

    def close_application(self, name: str) -> bool:
        app_name = name.strip().lower()
        process_name = self.process_map.get(app_name, f"{app_name}.exe")
        try:
            subprocess.run(
                ["taskkill", "/IM", process_name, "/F"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            return True
        except Exception as exc:
            LOGGER.error("Failed to close application '%s': %s", name, exc)
            return False

    def open_in_explorer(self, path: str) -> bool:
        target = Path(path).expanduser()
        try:
            if target.exists():
                subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["explorer", path])
            return True
        except Exception as exc:
            LOGGER.error("Failed opening explorer for '%s': %s", path, exc)
            return False

    def run_command(self, command: str) -> str:
        command = command.strip()
        if not command:
            return "No se recibio ningun comando."

        first_token = shlex.split(command, posix=False)[0].lower()
        if first_token not in self.WHITELIST_COMMANDS:
            return f"Comando bloqueado por seguridad. Permitidos: {', '.join(sorted(self.WHITELIST_COMMANDS))}"

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = result.stdout.strip() or result.stderr.strip()
            if not output:
                return "Comando ejecutado sin salida."
            return output[:4000]
        except subprocess.TimeoutExpired:
            return "El comando excedio el timeout de 10 segundos."
        except Exception as exc:
            return f"Error al ejecutar comando: {exc}"

    def take_screenshot(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.screenshot_dir / f"screenshot_{timestamp}.png"
        screenshot = pyautogui.screenshot()
        screenshot.save(output_path)
        return str(output_path)

    def get_system_status(self) -> dict[str, Any]:
        disk_root = Path.home().drive + "\\"
        disk_usage = psutil.disk_usage(disk_root)
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.4),
            "ram_percent": psutil.virtual_memory().percent,
            "disk_free_gb": round(disk_usage.free / (1024**3), 2),
        }
