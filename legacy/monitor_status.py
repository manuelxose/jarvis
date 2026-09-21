from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import psutil


def safe_print(message: str = "") -> None:
    try:
        print(message, flush=True)
    except OSError:
        # Some restricted console hosts may expose invalid stdout handles.
        pass


def test_ollama_api() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def process_snapshot(process_name: str) -> dict[str, float | int] | None:
    matches = []
    for proc in psutil.process_iter(attrs=["name", "memory_info", "cpu_times"]):
        name = (proc.info.get("name") or "").lower()
        if name == process_name.lower():
            matches.append(proc)

    if not matches:
        return None

    cpu_seconds = 0.0
    mem_bytes = 0
    for proc in matches:
        try:
            cpu_times = proc.cpu_times()
            cpu_seconds += float(cpu_times.user + cpu_times.system)
        except (psutil.Error, AttributeError):
            pass
        try:
            mem_bytes += int(proc.memory_info().rss)
        except (psutil.Error, AttributeError):
            pass

    return {
        "count": len(matches),
        "cpu_seconds": round(cpu_seconds, 1),
        "working_set_mb": round(mem_bytes / (1024 * 1024), 1),
    }


def tail_lines(path: Path, limit: int = 8) -> list[str]:
    if not path.exists():
        return [f"Log aun no disponible: {path}"]

    lines: deque[str] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\r\n"))
    except OSError as exc:
        return [f"No se pudo leer log: {exc}"]

    if not lines:
        return ["Log vacio por ahora."]
    return list(lines)


def render_state(project_root: Path, log_path: Path) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    ollama_up = test_ollama_api()
    ollama = process_snapshot("ollama.exe")
    python = process_snapshot("python.exe")

    safe_print("------------------------------------------------------------")
    safe_print(f"Jarvis Monitor - {now}")
    safe_print(f"ProjectRoot: {project_root}")
    safe_print(f"LogPath:     {log_path}")
    safe_print("")
    safe_print(f"Ollama API:  {'UP' if ollama_up else 'DOWN'}")

    if ollama:
        safe_print(
            "ollama.exe:  count={count} cpu_s={cpu_seconds} ram_mb={working_set_mb}".format(**ollama),
        )
    else:
        safe_print("ollama.exe:  no encontrado")

    if python:
        safe_print(
            "python.exe:  count={count} cpu_s={cpu_seconds} ram_mb={working_set_mb}".format(**python),
        )
    else:
        safe_print("python.exe:  no encontrado")

    safe_print("")
    safe_print("Ultimos eventos (jarvis.log):")
    for line in tail_lines(log_path, limit=8):
        safe_print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis runtime monitor")
    parser.add_argument("--project-root", required=True, help="Project root path")
    parser.add_argument("--log-path", required=True, help="Jarvis log file path")
    parser.add_argument("--refresh-seconds", type=float, default=2.0, help="Refresh interval")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    log_path = Path(args.log_path).resolve()
    refresh = max(1.0, float(args.refresh_seconds))

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    safe_print("Jarvis monitor iniciado. Ctrl+C para salir.")
    try:
        while True:
            render_state(project_root=project_root, log_path=log_path)
            time.sleep(refresh)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
