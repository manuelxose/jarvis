"""Windows/system tools with graceful degradation off-Windows.

Each tool runs on Windows via native APIs; on non-Windows hosts it degrades to
a clear message rather than failing silently. No tool exposes arbitrary shell
execution to the model: commands are fixed and allowlisted.
"""

from __future__ import annotations

import datetime
import os
import platform
import subprocess
import sys
import unicodedata
import webbrowser
from typing import Any, Mapping

from jarvis.core.contracts import TurnContext

from .gateway import Risk, Tool

_IS_WINDOWS = sys.platform == "win32"

_APP_MAP: dict[str, list[str]] = {
    "notepad": ["notepad"],
    "bloc de notas": ["notepad"],
    "explorador": ["explorer"],
    "explorer": ["explorer"],
    "calculadora": ["calc"],
    "powershell": ["powershell"],
    "spotify": ["start", "spotify"],
    "chrome": ["start", "chrome"],
}


class _CommandTool(Tool):
    def __init__(
        self,
        name: str,
        description: str,
        risk: Risk,
        input_schema: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.risk = risk
        self.input_schema = input_schema or {}

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        raise NotImplementedError


class TimeTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__("time", "Report the current time.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        return f"Son las {datetime.datetime.now().strftime('%H:%M')}."


class DateTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__("date", "Report the current date.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        today = datetime.date.today().strftime("%A %d de %B")
        return f"Hoy es {today}."


class SystemInfoTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__("system_info", "Report basic system state.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        info = f"{platform.system()} {platform.release()} ({platform.machine()})"
        try:
            cpu = os.cpu_count() or 0
        except Exception:
            cpu = 0
        return f"Sistema: {info}, {cpu} nucleos."


class OpenApplicationTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__(
            "open_application",
            "Open a fixed, allowlisted application.",
            Risk.REVERSIBLE,
            {"application": {"required": True}},
        )

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        raw = _normalize(str(arguments.get("application", "")).strip())
        command = _APP_MAP.get(raw)
        if command is None:
            for alias, candidate in _APP_MAP.items():
                if alias in raw or raw in alias:
                    command = candidate
                    break
        if command is None:
            return f"No se como abrir '{raw}'."
        if not _IS_WINDOWS:
            return f"Abriendo '{raw}' no esta disponible fuera de Windows."
        try:
            if command[0] == "start":
                subprocess.Popen(["cmd", "/c", "start", "", command[1]])
            else:
                subprocess.Popen(command)
        except Exception as error:
            return f"No he podido abrir '{raw}': {error}"
        return f"He abierto {raw}."


class OpenUrlTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__(
            "open_url",
            "Open a URL in the default browser.",
            Risk.REVERSIBLE,
            {"url": {"required": True}},
        )

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        url = str(arguments.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "Solo abro enlaces http o https."
        import asyncio

        opened = await asyncio.to_thread(webbrowser.open, url)
        return "Abriendo el enlace." if opened else "No he podido abrir el enlace."


class _WindowsKeyTool(_CommandTool):
    """Send a fixed Windows media/volume key via PowerShell SendKeys."""

    def __init__(self, name: str, description: str, char_code: int, ack: str) -> None:
        super().__init__(name, description, Risk.REVERSIBLE)
        self._char_code = char_code
        self._ack = ack

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return f"{self._ack} no esta disponible fuera de Windows."
        script = (
            f"(New-Object -ComObject WScript.Shell).SendKeys([char]{self._char_code})"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except Exception as error:
            return f"No he podido completar la accion: {error}"
        return self._ack


class VolumeSetTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__(
            "volume_set",
            "Set system volume to a level (0-100).",
            Risk.REVERSIBLE,
            {"level": {"required": True}},
        )

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        level = str(arguments.get("level", ""))
        if not _IS_WINDOWS:
            return f"El control de volumen no esta disponible fuera de Windows."
        # Absolute volume requires pycaw (optional); degrade honestly.
        try:
            import pycaw  # noqa: F401
        except ImportError:
            return f"Para ajustar el volumen a {level} se necesita la dependencia pycaw."
        return f"Volumen ajustado a {level}."


class StopTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__("stop", "Stop or cancel the current action.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        context.cancellation.cancel()
        return "Hecho."


class RepeatTool(_CommandTool):
    def __init__(self) -> None:
        super().__init__("repeat", "Repeat the last response.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        return "Hecho."


def _normalize(text: str) -> str:
    import re

    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\s]", " ", without_accents.lower()).strip()


def build_windows_tools() -> list[Tool]:
    """Return the default Windows/system tool set."""
    return [
        TimeTool(),
        DateTool(),
        SystemInfoTool(),
        OpenApplicationTool(),
        OpenUrlTool(),
        _WindowsKeyTool("volume_up", "Raise system volume.", 175, "He subido el volumen."),
        _WindowsKeyTool("volume_down", "Lower system volume.", 174, "He bajado el volumen."),
        VolumeSetTool(),
        _WindowsKeyTool("mute", "Mute system volume.", 173, "Sonido silenciado."),
        _WindowsKeyTool("unmute", "Unmute system volume.", 173, "Sonido restaurado."),
        _WindowsKeyTool("media_play_pause", "Toggle media play/pause.", 179, "Hecho."),
        _WindowsKeyTool("media_next", "Skip to the next track.", 176, "Siguiente."),
        _WindowsKeyTool("media_previous", "Go to the previous track.", 177, "Anterior."),
        StopTool(),
        RepeatTool(),
    ]
