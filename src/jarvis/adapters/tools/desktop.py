"""Owner-authorized Windows desktop tools.

Least-intrusive API per operation: Win32 window messages via pywin32 for
windows, ``psutil`` for processes, ``nvidia-smi`` for the GPU, the shell's
recycle bin for deletes, and CLI integrations for VS Code / Windows Terminal.
No screen-coordinate automation. Every tool is strict (unknown arguments are
rejected), returns a :class:`ToolResult`, and declares a per-call risk that
the :class:`ToolGateway` enforces. Windows-only modules are lazy-imported so
validation, risk classification and file operations are tested on any OS.
"""

from __future__ import annotations

import asyncio
import datetime
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

from jarvis.core.contracts import TurnContext

from .gateway import Risk, Tool, ToolResult

_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}

# Files whose contents are credentials: reading or writing them is HIGH risk.
_SECRET_FILE = re.compile(
    r"(^\.env(\..*)?$|^id_(rsa|ed25519|ecdsa)|\.pem$|\.key$|\.pfx$|\.p12$|credentials|secrets?\.|"
    r"^config\.local\.json$|\.kdbx$|^\.git-credentials$|^\.npmrc$|^\.pypirc$)",
    re.IGNORECASE,
)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".venv-tts", "dist", "build", ".next", "target"}


WSL_DISTRO = os.environ.get("JARVIS_WSL_DISTRO", "Ubuntu")


def wsl_posix(raw: str) -> Optional[str]:
    """'/home/x' or 'vscode-remote://wsl+ubuntu/home/x' -> '/home/x' (else None)."""
    raw = urllib.parse.unquote(str(raw))
    match = re.match(r"^vscode-remote://wsl\+[^/]+(/.*)$", raw, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return raw if raw.startswith(("/home/", "/mnt/", "/opt/", "/srv/", "/root")) else None


def windows_path_for(raw: str) -> str:
    """Map a WSL path to its \\wsl.localhost share on Windows (identity elsewhere)."""
    posix = wsl_posix(raw)
    if posix is None or not _IS_WINDOWS:
        return raw
    return "\\\\wsl.localhost\\" + WSL_DISTRO + posix.replace("/", "\\")


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


def is_secret_file(path: str | Path) -> bool:
    """Return True for credential-looking files (``.env``, keys, ``config.local.json``...)."""
    return bool(_SECRET_FILE.search(Path(path).name))


class DesktopContext:
    """Small persisted memory for conversational references ("that project")."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self.data: dict[str, Any] = {}
        if path is not None:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self.data = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def remember(self, **values: Any) -> None:
        self.data.update(values)
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self.data, ensure_ascii=False, default=str), encoding="utf-8")
        except OSError:
            pass

    def summary(self) -> dict[str, Any]:
        recent = recent_vscode_folders()
        return {
            "last_project": self.get("last_project") or (recent[0] if recent else None),
            "recent_vscode_folders": recent[:5],
            "last_profile": self.get("last_profile"),
            "last_file": self.get("last_file"),
            "last_command": self.get("last_command"),
        }


def recent_vscode_folders() -> list[str]:
    """Folders VS Code has open/last opened (its own storage.json)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    storage = Path(appdata) / "Code" / "User" / "globalStorage" / "storage.json"
    try:
        data = json.loads(storage.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    state = data.get("windowsState", {}) if isinstance(data, dict) else {}
    windows = [state.get("lastActiveWindow", {})] + list(state.get("openedWindows", []) or [])
    folders: list[str] = []
    for window in windows:
        uri = (window or {}).get("folder") or (window or {}).get("folderUri")
        if not isinstance(uri, str):
            continue
        if uri.startswith("file:///"):
            path = urllib.parse.unquote(uri[len("file:///"):]).replace("/", "\\")
        else:
            path = wsl_posix(uri)  # the owner works in VS Code over WSL remote
        if path and path not in folders:
            folders.append(path)
    return folders


class DesktopTool(Tool):
    """Base class for desktop tools: name, spoken description, risk and argument schema."""
    strict = True

    def __init__(self, name: str, description: str, risk: Risk, schema: Mapping[str, Any] | None = None, *, timeout: float = 30.0) -> None:
        self.name = name
        self.description = description
        self.risk = risk
        self.input_schema = dict(schema or {})
        self.timeout_seconds = timeout


def _windows_only(action: str) -> ToolResult:
    return ToolResult(f"{action} solo está disponible en Windows.", ok=False)


# -- windows & apps -------------------------------------------------------------

def _windows() -> list[tuple[int, str, int]]:
    from jarvis.application.workspace import window_titles  # noqa: PLC0415

    return window_titles()


def _process_names() -> dict[int, str]:
    import psutil  # noqa: PLC0415

    return {p.pid: (p.info.get("name") or "") for p in psutil.process_iter(["name"])}


_WINDOW_ALIASES = {"vs code": "visual studio code", "vscode": "visual studio code", "visual": "visual studio code"}


def find_windows(target: str) -> list[tuple[int, str, int]]:
    """Windows whose title or executable name contains *target*."""
    wanted = _norm(target).removesuffix(".exe")
    wanted = _WINDOW_ALIASES.get(wanted, wanted)
    if not wanted:
        return []
    names = _process_names()
    return [
        (hwnd, title, pid)
        for hwnd, title, pid in _windows()
        if wanted in _norm(title) or wanted in _norm(names.get(pid, "")).removesuffix(".exe")
    ]


def _focus(hwnd: int) -> None:
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - foreground lock: an Alt tap lets us take focus
        _keys([0x12])
        win32gui.SetForegroundWindow(hwnd)


def _keys(codes: list[int]) -> None:
    """Press a key chord via keybd_event (documented; no coordinates)."""
    import win32api  # noqa: PLC0415
    import win32con  # noqa: PLC0415

    for code in codes:
        win32api.keybd_event(code, 0, 0, 0)
    for code in reversed(codes):
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)


class FocusWindowTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("window_focus", "Bring an application window to the front.", Risk.REVERSIBLE,
                         {"target": {"required": True, "type": "string", "max_length": 120}})

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("Enfocar ventanas")
        found = await asyncio.to_thread(find_windows, arguments["target"])
        if not found:
            return ToolResult(f"No encuentro ninguna ventana de {arguments['target']}.", ok=False)
        await asyncio.to_thread(_focus, found[0][0])
        return ToolResult(f"Mostrando {found[0][1]}.", {"hwnd": found[0][0], "title": found[0][1]})


_WINDOW_ACTIONS = ("minimize", "maximize", "restore", "left", "right", "close")


class WindowTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__(
            "window_manage",
            "Minimize, maximize, restore, snap left/right or gracefully close an app's windows.",
            Risk.REVERSIBLE,
            {
                "target": {"required": True, "type": "string", "max_length": 120},
                "action": {"required": True, "type": "string", "enum": list(_WINDOW_ACTIONS)},
                "all": {"type": "boolean"},
            },
        )

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("Gestionar ventanas")
        found = await asyncio.to_thread(find_windows, arguments["target"])
        if not found:
            return ToolResult(f"No encuentro ninguna ventana de {arguments['target']}.", ok=False)
        targets = found if arguments.get("all") or arguments["action"] == "close" else found[:1]
        await asyncio.to_thread(self._apply, arguments["action"], [h for h, _, _ in targets])
        verbs = {"minimize": "Minimizado", "maximize": "Maximizado", "restore": "Restaurado", "left": "A la izquierda", "right": "A la derecha", "close": "Cerrando"}
        return ToolResult(f"{verbs[arguments['action']]}: {targets[0][1]}" + (f" y {len(targets) - 1} más." if len(targets) > 1 else "."), {"windows": len(targets)})

    @staticmethod
    def _apply(action: str, handles: list[int]) -> None:
        import win32con  # noqa: PLC0415
        import win32gui  # noqa: PLC0415

        for hwnd in handles:
            if action == "close":
                # WM_CLOSE: the app still asks about unsaved work (never a kill).
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                except Exception:  # noqa: BLE001 - elevated window (Task Manager): Windows refuses
                    continue
            elif action in ("minimize", "maximize", "restore"):
                win32gui.ShowWindow(hwnd, {"minimize": win32con.SW_MINIMIZE, "maximize": win32con.SW_MAXIMIZE, "restore": win32con.SW_RESTORE}[action])
            else:
                _focus(hwnd)
                _keys([0x5B, 0x25 if action == "left" else 0x27])  # Win+Left / Win+Right snap


class VirtualDesktopTool(DesktopTool):
    _CHORDS = {
        "next": [0x5B, 0x11, 0x27],  # Win+Ctrl+Right
        "previous": [0x5B, 0x11, 0x25],
        "new": [0x5B, 0x11, 0x44],  # Win+Ctrl+D
        "close": [0x5B, 0x11, 0x73],  # Win+Ctrl+F4 (windows move to the neighbour)
        "overview": [0x5B, 0x09],  # Win+Tab
    }

    def __init__(self) -> None:
        super().__init__("virtual_desktop", "Switch, create, close virtual desktops or show Task View.", Risk.REVERSIBLE,
                         {"action": {"required": True, "type": "string", "enum": list(self._CHORDS)}})

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("Los escritorios virtuales")
        # No public Win32 API switches virtual desktops; the documented shortcuts are the stable path.
        await asyncio.to_thread(_keys, self._CHORDS[arguments["action"]])
        return ToolResult("Hecho.")


class ListWindowsTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("windows_list", "List open windows and the foreground window.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("Listar ventanas")
        return await asyncio.to_thread(describe_desktop)


def describe_desktop() -> ToolResult:
    """Foreground window and open applications, as a spoken summary plus raw data."""
    import win32gui  # noqa: PLC0415

    names = _process_names()
    windows = [{"title": t, "process": names.get(pid, ""), "pid": pid} for _, t, pid in _windows()]
    foreground = win32gui.GetWindowText(win32gui.GetForegroundWindow())
    apps = sorted({w["process"].removesuffix(".exe") for w in windows if w["process"]})
    return ToolResult(f"En primer plano: {foreground or 'nada'}. Abiertas: {', '.join(apps[:8])}.", {"foreground": foreground, "windows": windows})


class CloseAppTool(DesktopTool):
    """Graceful close is LOW; force-killing a process is HIGH (unsaved work is lost)."""

    def __init__(self) -> None:
        super().__init__("app_close", "Close an application gracefully, or force-kill it (asks first).", Risk.REVERSIBLE,
                         {"target": {"required": True, "type": "string", "max_length": 120}, "force": {"type": "boolean"}})

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        return Risk.HIGH_RISK if arguments.get("force") else Risk.REVERSIBLE

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return f"forzar el cierre de {arguments['target']}; se perderá cualquier trabajo sin guardar"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("Cerrar aplicaciones")
        if not arguments.get("force"):
            return await WindowTool().execute({"target": arguments["target"], "action": "close"}, context)
        import psutil  # noqa: PLC0415

        wanted = _norm(arguments["target"]).removesuffix(".exe")
        victims = [p for p in psutil.process_iter(["name"]) if _norm(p.info.get("name") or "").removesuffix(".exe") == wanted]
        for proc in victims:
            proc.kill()
        return ToolResult(f"He forzado el cierre de {len(victims)} proceso(s) de {arguments['target']}.", {"killed": len(victims)}, ok=bool(victims))


# -- files ------------------------------------------------------------------------

class _FileTool(DesktopTool):
    def __init__(self, *args: Any, scopes: tuple[Path, ...], backup_dir: Path, memory: DesktopContext, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scopes = scopes
        self.backup_dir = backup_dir
        self.memory = memory

    def _path(self, raw: str) -> Path:
        path = Path(os.path.expandvars(windows_path_for(raw))).expanduser()
        if not path.is_absolute():
            base = Path(self.memory.get("last_project") or (self.scopes[0] if self.scopes else Path.cwd()))
            path = base / path
        return path.resolve()

    def _backup(self, path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.backup_dir / stamp / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if path.is_dir() else shutil.copy2)(path, target)
        return target


class FileSearchTool(_FileTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("file_search", "Find files by name pattern or text under a folder.", Risk.READ_ONLY,
                         {"pattern": {"required": True, "type": "string", "max_length": 200},
                          "root": {"type": "string", "max_length": 400},
                          "limit": {"type": "integer", "min": 1, "max": 100}}, timeout=20.0, **kwargs)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        roots = [self._path(arguments["root"])] if arguments.get("root") else list(self.scopes) or [Path.home()]
        pattern = arguments["pattern"]
        glob = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
        limit = arguments.get("limit") or 20
        found = await asyncio.to_thread(self._walk, roots, glob.casefold(), limit, context)
        if found:
            self.memory.remember(last_file=str(found[0]))
        names = ", ".join(p.name for p in found[:5])
        return ToolResult(f"He encontrado {len(found)} resultado(s)" + (f": {names}." if found else "."), {"paths": [str(p) for p in found]})

    @staticmethod
    def _walk(roots: list[Path], glob: str, limit: int, context: TurnContext) -> list[Path]:
        found: list[Path] = []
        deadline = time.monotonic() + 15
        for root in roots:
            for current, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
                for name in files + dirs:
                    if fnmatch.fnmatch(name.casefold(), glob):
                        found.append(Path(current) / name)
                        if len(found) >= limit:
                            return found
                if time.monotonic() > deadline or context.cancellation.cancelled:
                    return found
        return found


class FileReadTool(_FileTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("file_read", "Read a text file (credential files need confirmation).", Risk.READ_ONLY,
                         {"path": {"required": True, "type": "string", "max_length": 400}}, **kwargs)

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        return Risk.HIGH_RISK if is_secret_file(arguments.get("path", "")) else Risk.READ_ONLY

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return f"leer {arguments['path']}, que parece contener credenciales"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        path = self._path(arguments["path"])
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ToolResult(f"No existe {path.name}.", ok=False)
        except OSError as error:
            return ToolResult(f"No he podido leer {path.name}: {error}", ok=False)
        self.memory.remember(last_file=str(path))
        preview = text[:1000] + ("..." if len(text) > 1000 else "")
        return ToolResult(preview, {"path": str(path), "chars": len(text), "text": text[:20000]})


class FileWriteTool(_FileTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("file_write", "Create, overwrite or append to a text file (backed up first).", Risk.MEDIUM,
                         {"path": {"required": True, "type": "string", "max_length": 400},
                          "content": {"required": True, "type": "string", "max_length": 1_000_000},
                          "mode": {"type": "string", "enum": ["create", "overwrite", "append"]}}, **kwargs)

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        return Risk.HIGH_RISK if is_secret_file(arguments.get("path", "")) else Risk.MEDIUM

    def scope_paths(self, arguments: Mapping[str, Any]) -> list[str]:
        return [str(self._path(arguments["path"]))]

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return f"escribir en {arguments['path']}"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        path, mode = self._path(arguments["path"]), arguments.get("mode") or "overwrite"
        if mode == "create" and path.exists():
            return ToolResult(f"{path.name} ya existe; no lo sobrescribo.", ok=False)
        return await asyncio.to_thread(self._write, path, arguments["content"], mode)

    def _write(self, path: Path, content: str, mode: str) -> ToolResult:
        backup = self._backup(path)
        if mode == "append" and path.exists():
            content = path.read_text(encoding="utf-8", errors="replace") + content
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: an interrupted write never leaves a truncated file.
        tmp = path.with_name(f".{path.name}.jarvis-tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        self.memory.remember(last_file=str(path))
        return ToolResult(f"He guardado {path.name}.", {"path": str(path), "backup": str(backup) if backup else None})


class FileMoveTool(_FileTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("file_move", "Move or rename a file or folder.", Risk.MEDIUM,
                         {"source": {"required": True, "type": "string", "max_length": 400},
                          "destination": {"required": True, "type": "string", "max_length": 400},
                          "overwrite": {"type": "boolean"}}, **kwargs)

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        return Risk.HIGH_RISK if arguments.get("overwrite") else Risk.MEDIUM

    def scope_paths(self, arguments: Mapping[str, Any]) -> list[str]:
        return [str(self._path(arguments["source"])), str(self._path(arguments["destination"]))]

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return f"mover {arguments['source']} a {arguments['destination']}" + (" sobrescribiendo el destino" if arguments.get("overwrite") else "")

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        source, destination = self._path(arguments["source"]), self._path(arguments["destination"])
        if not source.exists():
            return ToolResult(f"No existe {source.name}.", ok=False)
        if destination.is_dir():
            destination = destination / source.name
        if destination.exists() and not arguments.get("overwrite"):
            return ToolResult(f"Ya existe {destination.name}; no lo sobrescribo.", ok=False)
        backup = await asyncio.to_thread(self._backup, destination) if destination.exists() else None
        await asyncio.to_thread(shutil.move, str(source), str(destination))
        return ToolResult(f"Movido a {destination}.", {"from": str(source), "to": str(destination), "backup": str(backup) if backup else None})


class FileDeleteTool(_FileTool):
    """Recycle bin delete is MEDIUM (undoable); permanent delete is HIGH."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("file_delete", "Send a file/folder to the Recycle Bin, or delete permanently (asks first).", Risk.MEDIUM,
                         {"path": {"required": True, "type": "string", "max_length": 400}, "permanent": {"type": "boolean"}}, **kwargs)

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        return Risk.HIGH_RISK if arguments.get("permanent") or is_secret_file(arguments.get("path", "")) else Risk.MEDIUM

    def scope_paths(self, arguments: Mapping[str, Any]) -> list[str]:
        return [str(self._path(arguments["path"]))]

    def describe(self, arguments: Mapping[str, Any]) -> str:
        path = self._path(arguments["path"])
        kind = "la carpeta" if path.is_dir() else "el archivo"
        how = "de forma permanente, sin papelera" if arguments.get("permanent") else "a la papelera"
        return f"borrar {kind} {path} {how}"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        path = self._path(arguments["path"])
        if not path.exists():
            return ToolResult(f"No existe {path.name}.", ok=False)
        if any(path == scope for scope in self.scopes) or path == Path.home() or len(path.parts) <= 2:
            return ToolResult("No borro raíces de unidad, la carpeta personal ni un ámbito autorizado completo.", ok=False)
        if arguments.get("permanent"):
            await asyncio.to_thread(shutil.rmtree if path.is_dir() else os.remove, path)
            return ToolResult(f"He borrado {path.name} definitivamente.", {"path": str(path)})
        where = await asyncio.to_thread(self._recycle, path)
        return ToolResult(f"He enviado {path.name} a la papelera.", {"path": str(path), "recycled_to": where})

    def _recycle(self, path: Path) -> str:
        if _IS_WINDOWS:
            from win32com.shell import shell, shellcon  # noqa: PLC0415

            flags = shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
            code, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, str(path), None, flags, None, None))
            if code or aborted:
                raise OSError(f"recycle failed with code {code}")
            return "recycle_bin"
        target = self.backup_dir / "trash" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        return str(target)


# -- developer tools -----------------------------------------------------------------

def _code_cli() -> str:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
    return str(local) if local.is_file() else (shutil.which("code") or "code")


class VSCodeTool(_FileTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("vscode_open", "Open a folder or file (optionally at a line) in VS Code.", Risk.REVERSIBLE,
                         {"path": {"required": True, "type": "string", "max_length": 400},
                          "line": {"type": "integer", "min": 1, "max": 1_000_000},
                          "new_window": {"type": "boolean"}}, **kwargs)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        posix = wsl_posix(arguments["path"])
        if posix is not None:
            # WSL project: open it through VS Code's WSL remote, like the owner does.
            argv = [_code_cli(), "--remote", f"wsl+{WSL_DISTRO}", posix]
            await asyncio.to_thread(subprocess.Popen, argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_NO_WINDOW)
            self.memory.remember(last_project=posix)
            return ToolResult(f"Abriendo {Path(posix).name} en VS Code.", {"path": posix, "remote": f"wsl+{WSL_DISTRO}"})
        path = self._path(arguments["path"])
        if not path.exists():
            return ToolResult(f"No existe {path}.", ok=False)
        argv = [_code_cli()]
        argv += ["--new-window"] if arguments.get("new_window") else ["--reuse-window"] if path.is_file() else []
        argv += ["--goto", f"{path}:{arguments['line']}"] if arguments.get("line") else [str(path)]
        await asyncio.to_thread(subprocess.Popen, argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_NO_WINDOW)
        self.memory.remember(**({"last_project": str(path)} if path.is_dir() else {"last_file": str(path)}))
        return ToolResult(f"Abriendo {path.name} en VS Code.", {"path": str(path)})


class TerminalTool(_FileTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("terminal_open", "Open Windows Terminal (PowerShell) in a folder.", Risk.REVERSIBLE,
                         {"path": {"type": "string", "max_length": 400},
                          "shell": {"type": "string", "enum": ["powershell", "pwsh", "cmd"]}}, **kwargs)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("Abrir el terminal")
        raw = arguments.get("path") or str(self.memory.get("last_project") or Path.home())
        wt = shutil.which("wt")
        posix = wsl_posix(raw)
        if posix is not None and wt and not arguments.get("shell"):
            await asyncio.to_thread(subprocess.Popen, [wt, "wsl", "-d", WSL_DISTRO, "--cd", posix])
            return ToolResult(f"Terminal de WSL abierto en {Path(posix).name}.", {"path": posix})
        folder = self._path(raw)
        shell_name = arguments.get("shell") or "powershell"
        argv = [wt, "-d", str(folder), shell_name] if wt else ["cmd", "/c", "start", "", "/D", str(folder), shell_name]
        await asyncio.to_thread(subprocess.Popen, argv, **({} if wt else _NO_WINDOW))
        return ToolResult(f"Terminal abierto en {folder.name}.", {"path": str(folder)})


# Programs a voice command may run inside an authorized project (MEDIUM).
_DEV_PROGRAMS = {
    "git", "npm", "pnpm", "yarn", "npx", "node", "python", "py", "pytest", "pip", "uv", "dotnet",
    "cargo", "go", "make", "mvn", "gradle", "docker", "ollama", "nvidia-smi", "code", "tsc", "ruff", "mypy", "jarvis",
}
# Read-only invocations (LOW).
_READ_ONLY = re.compile(
    r"^(git (status|log|diff|branch|show|remote -v)|docker (ps|images|info)|nvidia-smi|ollama (list|ps)|"
    r"(python|py) --version|node --version|npm (ls|outdated))\b"
)
# Destructive or security-sensitive: always HIGH, even inside a project.
_DESTRUCTIVE = re.compile(
    r"(\brm\b|\bdel\b|\berase\b|\brmdir\b|\brd\b|\bformat\b|diskpart|bcdedit|\breg\b|netsh|\bsc\b|icacls|takeown|cipher|"
    r"remove-item|set-executionpolicy|shutdown|restart-computer|git push .*(--force|-f\b)|git reset --hard|git clean|"
    r"git branch -D|docker (system|volume|image) prune|docker (volume )?rm|npm publish|pip uninstall|--force\b|mkfs|\bdd\b|"
    r"invoke-expression|\biex\b|curl .*\|\s*(sh|bash|iex)|runas|sudo)",
    re.IGNORECASE,
)


def command_risk(argv: list[str]) -> Risk:
    """Classify a command line: known dev programs are MEDIUM, destructive or unknown ones HIGH."""
    if not argv:
        return Risk.HIGH_RISK
    line = " ".join(argv)
    program = Path(argv[0]).name.lower().removesuffix(".exe").removesuffix(".cmd")
    if _DESTRUCTIVE.search(line) or program in {"powershell", "pwsh", "cmd", "bash", "sh", "wsl"}:
        return Risk.HIGH_RISK
    if program not in _DEV_PROGRAMS:
        return Risk.HIGH_RISK
    normalized = " ".join([program, *argv[1:]])
    return Risk.READ_ONLY if _READ_ONLY.match(normalized) else Risk.MEDIUM


def _argv(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return shlex.split(str(value), posix=not _IS_WINDOWS)


class RunCommandTool(_FileTool):
    """Run one program (no shell) in a project folder; output tail is kept for 'fix this error'."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("run_command", "Run a developer command (git, npm, pytest...) in a project folder.", Risk.MEDIUM,
                         {"command": {"required": True, "max_length": 2000},
                          "cwd": {"type": "string", "max_length": 400},
                          "timeout_seconds": {"type": "number", "min": 1, "max": 600}}, timeout=610.0, **kwargs)

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        try:
            return command_risk(_argv(arguments.get("command", "")))
        except ValueError:
            return Risk.HIGH_RISK

    def _cwd(self, arguments: Mapping[str, Any]) -> Path:
        return self._path(arguments.get("cwd") or str(self.memory.get("last_project") or (self.scopes[0] if self.scopes else Path.cwd())))

    def scope_paths(self, arguments: Mapping[str, Any]) -> list[str]:
        return [str(self._cwd(arguments))]

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return f"ejecutar «{' '.join(_argv(arguments['command']))}» en {self._cwd(arguments)}"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        argv, cwd = _argv(arguments["command"]), self._cwd(arguments)
        if not cwd.is_dir():
            return ToolResult(f"No existe la carpeta {cwd}.", ok=False)
        resolved = shutil.which(argv[0]) or argv[0]
        process = await asyncio.create_subprocess_exec(
            resolved, *argv[1:], cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **_NO_WINDOW
        )
        timeout = float(arguments.get("timeout_seconds") or 300)
        try:
            output, _ = await asyncio.wait_for(self._communicate(process, context), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.to_thread(_kill_tree, process.pid)
            raise
        text = output.decode("utf-8", errors="replace")
        tail = "\n".join(text.strip().splitlines()[-40:])
        record = {"argv": argv, "cwd": str(cwd), "exit_code": process.returncode, "tail": tail[-4000:]}
        self.memory.remember(last_command=record, last_project=str(cwd))
        ok = process.returncode == 0
        say = "Comando completado." if ok else f"El comando ha fallado con código {process.returncode}."
        return ToolResult(say, record, ok=ok)

    @staticmethod
    async def _communicate(process: asyncio.subprocess.Process, context: TurnContext) -> tuple[bytes, bytes]:
        task = asyncio.ensure_future(process.communicate())
        while not task.done():
            if context.cancellation.cancelled:  # explicit "cancel the operation"
                task.cancel()
                raise asyncio.CancelledError
            await asyncio.wait({task}, timeout=0.2)
        return task.result()


def _kill_tree(pid: int) -> None:
    try:
        import psutil  # noqa: PLC0415

        proc = psutil.Process(pid)
        for child in proc.children(recursive=True) + [proc]:
            child.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass


# -- system ------------------------------------------------------------------------

def _nvidia_smi(*args: str) -> str:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        raise FileNotFoundError("nvidia-smi not found")
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=10, **_NO_WINDOW).stdout


def gpu_status() -> Optional[dict[str, Any]]:
    """Name, utilization, VRAM, temperature and clock of the GPU via ``nvidia-smi``, or None."""
    try:
        line = _nvidia_smi("--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,clocks.sm", "--format=csv,noheader,nounits").strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    name, util, used, total, temp, clock = [p.strip() for p in line.split(",")]
    return {"name": name, "util_percent": _num(util), "memory_used_mb": _num(used), "memory_total_mb": _num(total), "temperature_c": _num(temp), "sm_clock_mhz": _num(clock)}


def gpu_free_mb() -> Optional[float]:
    """Free VRAM in MiB, or None when ``nvidia-smi`` is unavailable."""
    status = gpu_status()
    if not status or status["memory_total_mb"] is None or status["memory_used_mb"] is None:
        return None
    return status["memory_total_mb"] - status["memory_used_mb"]


def _num(value: str) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return None


class SystemStatsTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("system_stats", "CPU, memory and GPU usage.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        import psutil  # noqa: PLC0415

        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        memory = psutil.virtual_memory()
        gpu = await asyncio.to_thread(gpu_status)
        say = f"CPU al {cpu:.0f} por ciento, memoria al {memory.percent:.0f} por ciento"
        if gpu:
            say += f", GPU al {gpu['util_percent']:.0f} por ciento con {gpu['memory_used_mb'] / 1024:.1f} de {gpu['memory_total_mb'] / 1024:.0f} gigas y {gpu['temperature_c']:.0f} grados"
        return ToolResult(say + ".", {"cpu_percent": cpu, "memory_percent": memory.percent, "memory_used_gb": round(memory.used / 2**30, 1), "gpu": gpu})


class ProcessListTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("process_list", "Top processes by CPU or memory.", Risk.READ_ONLY,
                         {"sort": {"type": "string", "enum": ["cpu", "memory"]}, "limit": {"type": "integer", "min": 1, "max": 30}})

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        rows = await asyncio.to_thread(top_processes, arguments.get("sort") or "cpu", arguments.get("limit") or 8)
        key = "cpu_percent" if (arguments.get("sort") or "cpu") == "cpu" else "memory_mb"
        unit = "%" if key == "cpu_percent" else " MB"
        return ToolResult("Más consumo: " + ", ".join(f"{r['name']} {r[key]:.0f}{unit}" for r in rows[:5]) + ".", {"processes": rows})


def top_processes(sort: str, limit: int) -> list[dict[str, Any]]:
    """The *limit* processes using the most CPU or memory."""
    import psutil  # noqa: PLC0415

    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))
    if sort == "cpu":  # CPU needs two samples; memory does not
        for p in procs:
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(0.5)
    rows = []
    for p in procs:
        try:
            rows.append({"pid": p.pid, "name": p.info["name"], "cpu_percent": p.cpu_percent(None) / (psutil.cpu_count() or 1),
                         "memory_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / 2**20, 1)})
        except psutil.Error:
            continue
    rows.sort(key=lambda r: r["cpu_percent" if sort == "cpu" else "memory_mb"], reverse=True)
    return rows[:limit]


class GpuProcessesTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("gpu_processes", "Which processes use the GPU and how much VRAM.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        try:
            raw = await asyncio.to_thread(_nvidia_smi, "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits")
        except (OSError, subprocess.SubprocessError) as error:
            return ToolResult(f"No puedo consultar la GPU: {error}", ok=False)
        rows = parse_compute_apps(raw)
        gpu = await asyncio.to_thread(gpu_status)
        if not rows:
            say = "Ningún proceso de cómputo está usando la GPU."
        else:
            known = [r for r in rows if r["memory_mb"] is not None]
            # WDDM hides per-process VRAM on laptops: report the processes and the total.
            say = "Usan la GPU: " + ", ".join(
                f"{r['name']}" + (f" con {r['memory_mb']:.0f} megas" if r["memory_mb"] is not None else "") for r in rows[:5]
            )
            if not known and gpu:
                say += f". En total hay {gpu['memory_used_mb']:.0f} de {gpu['memory_total_mb']:.0f} megas ocupados"
            say += "."
        return ToolResult(say, {"processes": rows, "gpu": gpu})


def parse_compute_apps(raw: str) -> list[dict[str, Any]]:
    """Parse ``nvidia-smi --query-compute-apps`` CSV into process rows."""
    rows = []
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        name = parts[1]
        if name.startswith("["):  # WDDM hides names of other sessions' processes
            name = _pid_name(int(parts[0])) or name
        rows.append({"pid": int(parts[0]), "name": Path(name.replace("\\", "/")).name, "memory_mb": _num(parts[2])})
    rows.sort(key=lambda r: r["memory_mb"] or 0, reverse=True)
    return rows


def _pid_name(pid: int) -> Optional[str]:
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001 - gone or protected
        return None


class NetworkStatsTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("network_stats", "Current network download/upload rate.", Risk.READ_ONLY)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        rates = await asyncio.to_thread(network_rates)
        return ToolResult(f"Red: bajando {_rate(rates['down_bps'])} y subiendo {_rate(rates['up_bps'])}.", rates)


def network_rates(seconds: float = 1.0) -> dict[str, float]:
    """Download/upload rate sampled over *seconds*, plus session totals."""
    import psutil  # noqa: PLC0415

    first = psutil.net_io_counters()
    time.sleep(seconds)
    second = psutil.net_io_counters()
    return {"down_bps": (second.bytes_recv - first.bytes_recv) / seconds, "up_bps": (second.bytes_sent - first.bytes_sent) / seconds,
            "total_received_gb": round(second.bytes_recv / 2**30, 2), "total_sent_gb": round(second.bytes_sent / 2**30, 2)}


def _rate(bps: float) -> str:
    if bps >= 2**20:
        return f"{bps / 2**20:.1f} megas por segundo"
    return f"{bps / 2**10:.0f} kilobytes por segundo"


def posix_from_unc(path: str) -> Optional[str]:
    """\\\\wsl.localhost\\Ubuntu\\home\\x -> /home/x (None if not a WSL share)."""
    match = re.match(r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)$", str(path), flags=re.IGNORECASE)
    return match.group(1).replace("\\", "/") if match else None


class ProjectOpenTool(_FileTool):
    """Open a project folder by name from the authorized scopes (no LLM needed)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("project_open", "Open a project/repository by folder name in VS Code.", Risk.REVERSIBLE,
                         {"name": {"required": True, "type": "string", "max_length": 80}}, **kwargs)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        matches = await asyncio.to_thread(find_projects, list(self.scopes), arguments["name"])
        if not matches:
            return ToolResult(f"No encuentro ningún proyecto llamado {arguments['name']}.", ok=False)
        if len(matches) > 1 and _norm(matches[0].name) != _norm(arguments["name"]):
            names = ", ".join(m.name for m in matches[:3])
            return ToolResult(f"Hay varios: {names}. ¿Cuál abro?", {"candidates": [str(m) for m in matches]}, ok=False)
        target = str(matches[0])
        path = posix_from_unc(target) or target
        return await VSCodeTool(scopes=self.scopes, backup_dir=self.backup_dir, memory=self.memory).execute({"path": path}, context)


def find_projects(scopes: list[Path], name: str, depth: int = 2) -> list[Path]:
    """Folders under *scopes* whose name matches *name*, exact matches first."""
    wanted = re.sub(r"[\s_-]+", "", _norm(name))
    exact, partial = [], []
    for scope in scopes:
        frontier = [scope]
        for _ in range(depth):
            next_level = []
            for folder in frontier:
                try:
                    children = [c for c in folder.iterdir() if c.is_dir() and not c.name.startswith(".") and c.name not in _SKIP_DIRS]
                except OSError:
                    continue
                for child in children:
                    key = re.sub(r"[\s_-]+", "", _norm(child.name))
                    (exact if key == wanted else partial if wanted and wanted in key else []).append(child)
                next_level += children
            frontier = next_level
    return exact + sorted(partial, key=lambda p: len(p.name))


class ProcessKillTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("process_kill", "Terminate a process by pid (asks first).", Risk.HIGH_RISK,
                         {"pid": {"required": True, "type": "integer", "min": 5}})

    def describe(self, arguments: Mapping[str, Any]) -> str:
        try:
            import psutil  # noqa: PLC0415

            return f"terminar el proceso {psutil.Process(arguments['pid']).name()} (pid {arguments['pid']})"
        except Exception:  # noqa: BLE001
            return f"terminar el proceso con pid {arguments['pid']}"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        await asyncio.to_thread(_kill_tree, arguments["pid"])
        return ToolResult("Proceso terminado.")


class ScreenshotTool(DesktopTool):
    def __init__(self, directory: Path) -> None:
        super().__init__("screenshot", "Capture the screen and describe the open windows.", Risk.READ_ONLY)
        self.directory = directory

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("La captura de pantalla")
        from PIL import ImageGrab  # noqa: PLC0415

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"screen-{datetime.datetime.now():%Y%m%d-%H%M%S}.png"
        image = await asyncio.to_thread(ImageGrab.grab, all_screens=True)
        await asyncio.to_thread(image.save, path)
        desktop = await asyncio.to_thread(describe_desktop)
        return ToolResult(f"Captura guardada. {desktop.say}", {"path": str(path), **desktop.data})


class VolumeLevelTool(DesktopTool):
    """Absolute master volume: pycaw when installed, else 2%-step media keys."""

    def __init__(self) -> None:
        super().__init__("volume_level", "Set the master volume to 0-100.", Risk.REVERSIBLE,
                         {"level": {"required": True, "type": "integer", "min": 0, "max": 100}})

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if not _IS_WINDOWS:
            return _windows_only("El volumen")
        level = arguments["level"]
        await asyncio.to_thread(set_master_volume, level)
        return ToolResult(f"Volumen al {level} por ciento.")


def set_master_volume(level: int) -> None:
    """Set the master volume via pycaw, falling back to media-key steps."""
    try:
        from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

        AudioUtilities.GetSpeakers().EndpointVolume.SetMasterVolumeLevelScalar(level / 100, None)
        return
    except Exception:  # noqa: BLE001 - pycaw missing or older API: fall back to keys
        pass
    # ponytail: 50 x VOLUME_DOWN then level/2 x VOLUME_UP (2% per press). Exact to
    # +-2% and shows the OSD; install pycaw for silent exact levels.
    import win32api  # noqa: PLC0415
    import win32con  # noqa: PLC0415

    for code, times in ((0xAE, 50), (0xAF, round(level / 2))):
        for _ in range(times):
            win32api.keybd_event(code, 0, 0, 0)
            win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)


class WebSearchTool(DesktopTool):
    def __init__(self) -> None:
        super().__init__("web_search", "Search the web in the default browser.", Risk.REVERSIBLE,
                         {"query": {"required": True, "type": "string", "max_length": 300}})

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(arguments["query"])
        await asyncio.to_thread(webbrowser.open, url)
        return ToolResult(f"Buscando {arguments['query']}.", {"url": url})


class SleepTool(DesktopTool):
    """'Jarvis, a dormir': end the voice session (the daemon goes back to claps)."""

    def __init__(self, request_sleep: Callable[[], None]) -> None:
        super().__init__("sleep", "Stop listening until the next activation gesture.", Risk.READ_ONLY)
        self._request_sleep = request_sleep

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        self._request_sleep()
        return ToolResult("Hasta luego. Estaré atento.")


class MusicTool(DesktopTool):
    """Startup music: stop / lower / raise. Without startup music, media keys (Spotify...)."""

    def __init__(self, mixer: Any = None, media_key: Optional[Callable[[], Awaitable[Any]]] = None) -> None:
        super().__init__("music", "Stop, lower or raise the music that is playing.", Risk.REVERSIBLE,
                         {"action": {"required": True, "type": "string", "enum": ["stop", "lower", "raise"]}})
        self.mixer, self._media_key = mixer, media_key

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        action, mixer = arguments["action"], self.mixer
        if mixer is not None and getattr(mixer, "music_playing", False):
            if action == "stop":
                mixer.fade_out(1.2)
                return ToolResult("Música detenida.")
            level = mixer.gain * (0.5 if action == "lower" else 1.6)
            mixer.ramp(min(max(level, 0.05), 1.0), 0.4)
            return ToolResult("Música más baja." if action == "lower" else "Música más alta.")
        if action == "stop" and self._media_key is not None:
            await self._media_key()  # pause whatever player is active
            return ToolResult("Pausado.")
        return ToolResult("No está sonando la música de arranque.", ok=False)


class AssistantControlTool(DesktopTool):
    """'Reiníciate' / 'apágate': restart or fully stop the background assistant."""

    def __init__(self, action: str, callback: Optional[Callable[[], Any]] = None) -> None:
        name = "assistant_restart" if action == "restart" else "assistant_shutdown"
        risk = Risk.REVERSIBLE if action == "restart" else Risk.HIGH_RISK
        super().__init__(name, f"{action.capitalize()} the Jarvis background assistant.", risk)
        self.action, self.callback = action, callback

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return "apagarme por completo; para volver a usarme tendrás que arrancarme a mano o reiniciar la sesión de Windows"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        if self.callback is None:
            return ToolResult("Solo puedo reiniciarme cuando funciono en segundo plano.", ok=False)
        self.callback()  # takes effect after this reply has been spoken
        if self.action == "restart":
            return ToolResult("Reiniciando. En unos segundos vuelvo a estar atento a tus palmadas.")
        return ToolResult("Apagando. Hasta pronto.")


class CancelOperationsTool(DesktopTool):
    def __init__(self, cancel: Callable[[], int]) -> None:
        super().__init__("cancel_operations", "Cancel running operations (commands, launches).", Risk.READ_ONLY)
        self._cancel = cancel

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        count = self._cancel()
        return ToolResult("He cancelado la operación en curso." if count else "No hay ninguna operación en curso.", {"cancelled": count})


# -- workspace & services ------------------------------------------------------------

class WorkspaceTool(DesktopTool):
    def __init__(self, manager: Any, memory: DesktopContext, default_profile: str) -> None:
        super().__init__("workspace", "Start, stop or check a development workspace profile.", Risk.MEDIUM,
                         {"action": {"required": True, "type": "string", "enum": ["start", "stop", "status"]},
                          "profile": {"type": "string", "max_length": 60},
                          "force": {"type": "boolean"}}, timeout=300.0)
        self.manager, self.memory, self.default_profile = manager, memory, default_profile

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        if arguments.get("action") == "status":
            return Risk.READ_ONLY
        if arguments.get("action") == "stop" and arguments.get("force"):
            return Risk.HIGH_RISK  # would close apps the owner opened themselves
        return Risk.MEDIUM if arguments.get("action") == "stop" else Risk.REVERSIBLE

    def describe(self, arguments: Mapping[str, Any]) -> str:
        return f"cerrar todo el entorno {arguments.get('profile') or self.default_profile}, incluidas las aplicaciones que no abrí yo"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        from jarvis.application.workspace import summarize  # noqa: PLC0415

        profile = arguments.get("profile") or self.memory.get("last_profile") or self.default_profile
        if profile not in self.manager.profiles:
            return ToolResult(f"No conozco el perfil {profile}. Tengo: {', '.join(self.manager.profiles)}.", ok=False)
        action = arguments["action"]
        if action == "status":
            managed = self.manager.managed(profile)
            return ToolResult(f"En {profile} gestiono {len(managed)} proceso(s).", {"managed": managed})
        if action == "start":
            results = await self.manager.start(profile)
            self.memory.remember(last_profile=profile)
            return ToolResult(summarize(results), {"results": [r.__dict__ for r in results]}, ok=all(r.ok for r in results))
        results = await self.manager.stop(profile, force=bool(arguments.get("force")))
        stopped = [r.name for r in results if r.status == "stopped"]
        return ToolResult(("He cerrado " + ", ".join(stopped) + ".") if stopped else "No había nada mío que cerrar.", {"results": [r.__dict__ for r in results]})


class ServiceRestartTool(DesktopTool):
    def __init__(self, manager: Any, restarters: Mapping[str, Callable[[], Awaitable[Any]]]) -> None:
        super().__init__("service_restart", "Restart Hermes or a service from a workspace profile.", Risk.MEDIUM,
                         {"name": {"required": True, "type": "string", "max_length": 60}}, timeout=120.0)
        self.manager, self.restarters = manager, dict(restarters)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        from jarvis.application.workspace import summarize  # noqa: PLC0415

        name = _norm(arguments["name"])
        if name in self.restarters:
            await self.restarters[name]()
            return ToolResult(f"He reiniciado {arguments['name']}.")
        found = self.manager.find_task(name) if self.manager is not None else None
        if found is None:
            return ToolResult(f"No sé reiniciar {arguments['name']}.", ok=False)
        results = await self.manager.restart(found[0], found[1].name)
        return ToolResult(summarize(results), {"results": [r.__dict__ for r in results]}, ok=all(r.ok for r in results))


def build_desktop_tools(
    *,
    scopes: tuple[Path, ...],
    data_dir: Path,
    memory: DesktopContext,
    cancel_operations: Callable[[], int],
    workspace: Any = None,
    default_profile: str = "dev",
    restarters: Optional[Mapping[str, Callable[[], Awaitable[Any]]]] = None,
) -> list[Tool]:
    """Instantiate every desktop tool with its scopes, data directory and collaborators."""
    files = {"scopes": scopes, "backup_dir": data_dir / "backups", "memory": memory}
    tools: list[Tool] = [
        FocusWindowTool(), WindowTool(), VirtualDesktopTool(), ListWindowsTool(), CloseAppTool(),
        FileSearchTool(**files), FileReadTool(**files), FileWriteTool(**files), FileMoveTool(**files), FileDeleteTool(**files),
        VSCodeTool(**files), TerminalTool(**files), RunCommandTool(**files), ProjectOpenTool(**files),
        SystemStatsTool(), ProcessListTool(), GpuProcessesTool(), NetworkStatsTool(), ProcessKillTool(),
        ScreenshotTool(data_dir / "screenshots"), VolumeLevelTool(), WebSearchTool(), CancelOperationsTool(cancel_operations),
    ]
    if workspace is not None:
        tools.append(WorkspaceTool(workspace, memory, default_profile))
    tools.append(ServiceRestartTool(workspace, restarters or {}))
    return tools
