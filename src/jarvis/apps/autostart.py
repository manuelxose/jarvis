"""Run the sentinel after Windows sign-in, without a console window.

A shortcut in the per-user Startup folder launches ``pythonw.exe
scripts\\jarvis_daemon.pyw``. No scheduled task, no elevation, no UAC: the
Startup folder is the documented per-user mechanism and the owner can see or
remove the entry in Task Manager > Startup apps.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SHORTCUT_NAME = "Jarvis Sentinel.lnk"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = PROJECT_ROOT / "scripts" / "jarvis_daemon.pyw"


def startup_folder() -> Path:
    """The current user's Startup folder (Windows only)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; autostart is Windows-only")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def pythonw() -> Path:
    """``pythonw.exe`` next to the running interpreter, so the daemon starts without a console."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.is_file() else Path(sys.executable)


def unc(path: Path) -> Path:
    """Replace a mapped drive letter (e.g. pushd's temporary Z:) with its UNC share.

    The shortcut outlives the shell that created it; a temporary drive letter
    would not exist at the next sign-in.
    """
    path = Path(path).resolve()
    drive = path.drive
    if len(drive) == 2 and drive[1] == ":":
        try:
            import win32wnet  # noqa: PLC0415

            share = win32wnet.WNetGetConnection(drive)
        except Exception:  # noqa: BLE001 - a local disk has no network connection
            return path
        return Path(share + str(path)[2:])
    return path


def install(config_path: Path) -> Path:
    """Create the Startup-folder shortcut that launches the daemon at sign-in."""
    if sys.platform != "win32":
        raise RuntimeError("autostart is Windows-only")
    import win32com.client  # noqa: PLC0415

    target = startup_folder() / SHORTCUT_NAME
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(target))
    shortcut.TargetPath = str(unc(pythonw()))
    shortcut.Arguments = f'"{unc(LAUNCHER)}" --config "{unc(Path(config_path))}"'
    shortcut.WorkingDirectory = str(unc(PROJECT_ROOT))
    shortcut.Description = "Jarvis: triple-clap / Ctrl+Alt+J activation"
    shortcut.WindowStyle = 7  # minimized; pythonw has no window anyway
    shortcut.Save()
    return target


def remove() -> bool:
    """Delete the autostart shortcut; return True when one existed."""
    target = startup_folder() / SHORTCUT_NAME
    if target.exists():
        target.unlink()
        return True
    return False


def status() -> dict[str, object]:
    """Describe the autostart shortcut for ``jarvis autostart status``."""
    try:
        target = startup_folder() / SHORTCUT_NAME
    except RuntimeError as error:
        return {"installed": False, "detail": str(error)}
    return {"installed": target.exists(), "shortcut": str(target), "launcher": str(unc(LAUNCHER)), "pythonw": str(unc(pythonw()))}
