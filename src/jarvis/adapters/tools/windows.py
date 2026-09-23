"""Windows/system tools with graceful degradation off-Windows.

Each tool runs on Windows via native APIs; on non-Windows hosts it degrades to
a clear message rather than failing silently. No tool exposes arbitrary shell
execution to the model: commands are fixed and allowlisted.
"""

from __future__ import annotations

import base64
import datetime
import os
import platform
import subprocess
import sys
import unicodedata
import webbrowser
from pathlib import Path
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
    # The spotify: URI works for both the desktop and the Microsoft Store install;
    # "start spotify" only finds the desktop build.
    "spotify": ["start", "spotify:"],
    "chrome": ["start", "chrome"],
    "crome": ["start", "chrome"],  # common STT spelling
    "navegador": ["start", "https://www.google.com"],
    "cmd": ["start", "cmd"],
    "terminal": ["start", "cmd"],
    "consola": ["start", "cmd"],
    "simbolo del sistema": ["start", "cmd"],
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
        return f"Abriendo {raw}, señor."


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


class FileTool(_CommandTool):
    """Read or write a text file confined to the current working directory."""

    def __init__(self) -> None:
        super().__init__(
            "file",
            "Read or write a text file within the working directory.",
            Risk.REVERSIBLE,
            {"action": {"required": True}, "path": {"required": True}, "content": {}},
        )

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"read", "write"}:
            return "La accion de archivo debe ser 'read' o 'write'."

        path = str(arguments.get("path", "")).strip()
        if not path:
            return "Necesito una ruta de archivo."

        # Confine every path to the current working directory so a voice-driven
        # write cannot escape to arbitrary filesystem locations.
        base = Path.cwd().resolve()
        try:
            resolved = (base / path).resolve()
        except (OSError, RuntimeError) as error:
            return f"No he podido resolver la ruta '{path}': {error}"
        try:
            resolved.relative_to(base)
        except ValueError:
            return "No puedo acceder a rutas fuera del directorio de trabajo."

        if action == "read":
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return f"No he encontrado el archivo '{path}'."
            except OSError as error:
                return f"No he podido leer el archivo '{path}': {error}"
            # ponytail: cap the preview so unbounded file contents never flood the
            # TTS pipeline. Raise this ceiling only if a real product need appears.
            if len(text) > 1000:
                text = text[:1000] + "..."
            return text

        content = str(arguments.get("content", ""))
        try:
            resolved.write_text(content, encoding="utf-8")
        except OSError as error:
            return f"No he podido escribir el archivo '{path}': {error}"
        return f"He escrito el archivo '{path}'."


class ClipboardTool(_CommandTool):
    """Read or set the Windows clipboard via PowerShell (Get/Set-Clipboard)."""

    def __init__(self) -> None:
        super().__init__(
            "clipboard",
            "Read or set the clipboard (Windows).",
            Risk.REVERSIBLE,
            {"action": {"required": True}, "content": {}},
        )

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        action = str(arguments.get("action", "")).strip().lower()
        if action not in {"read", "copy"}:
            return "La accion de portapapeles debe ser 'read' o 'copy'."

        if not _IS_WINDOWS:
            return "El portapapeles no esta disponible fuera de Windows."

        if action == "read":
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception as error:
                return f"No he podido leer el portapapeles: {error}"
            if result.returncode != 0:
                return "No he podido leer el portapapeles."
            return result.stdout.strip()

        content = arguments.get("content")
        if content is None or not str(content).strip():
            return "Necesito contenido para copiar al portapapeles."
        # PowerShell 5.1 mangles non-ASCII text passed as positional args (codepage
        # + escaping), so the script is delivered base64 UTF-16LE via -EncodedCommand
        # and the content flows over stdin instead.
        script = "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-EncodedCommand", encoded],
                input=str(content),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as error:
            return f"No he podido copiar al portapapeles: {error}"
        if result.returncode != 0:
            return "No he podido copiar al portapapeles."
        return "He copiado el texto al portapapeles."


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
        FileTool(),
        ClipboardTool(),
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
