"""Supervised development-workspace profiles (VS Code, terminals, services, URLs).

A profile is a list of tasks. Each task may declare ``depends_on``; tasks
without a dependency relation start concurrently. Before launching, the task's
``detect`` probes decide whether it is already running (then it is left alone
and never shut down by Jarvis). After launching, ``ready`` probes are polled
until ``timeout_seconds``, with up to ``retries`` relaunches.

Only processes Jarvis launched itself are recorded as *managed* (pid +
creation time, persisted so a later process can shut them down safely); a
profile shutdown touches nothing else unless the owner passes ``force``.

Probe kinds: ``http`` (GET < 400), ``port`` (TCP connect), ``process``
(executable name), ``window_title`` (substring of a visible top-level window),
``command`` (argv exits 0). Windows specifics are lazy-imported so the logic
is unit-tested anywhere with injected probes/spawner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

logger = logging.getLogger("jarvis.workspace")

_IS_WINDOWS = sys.platform == "win32"
_PROBE_KINDS = ("http", "port", "process", "window_title", "command")
_WINDOW_MODES = ("gui", "hidden", "new_console")


@dataclass(frozen=True)
class TaskSpec:
    name: str
    command: tuple[str, ...] = ()
    url: str = ""
    cwd: str = ""
    depends_on: tuple[str, ...] = ()
    detect: Mapping[str, Any] = field(default_factory=dict)
    ready: Mapping[str, Any] = field(default_factory=dict)
    stop: Mapping[str, Any] = field(default_factory=dict)  # {"window_title": ...} = graceful close
    timeout_seconds: float = 30.0
    retries: int = 1
    required: bool = False
    window: str = "gui"


@dataclass(frozen=True)
class Profile:
    name: str
    tasks: tuple[TaskSpec, ...]
    description: str = ""


def parse_profiles(data: Mapping[str, Any]) -> dict[str, Profile]:
    """Validate the ``workspace.profiles`` config object."""
    if not isinstance(data, Mapping):
        raise ValueError("workspace.profiles must be an object")
    profiles: dict[str, Profile] = {}
    for name, raw in data.items():
        if not isinstance(raw, Mapping) or not isinstance(raw.get("tasks", []), list):
            raise ValueError(f"workspace profile {name!r} must be an object with a tasks list")
        tasks = tuple(_parse_task(name, item) for item in raw.get("tasks", []))
        names = [t.name for t in tasks]
        if len(set(names)) != len(names):
            raise ValueError(f"workspace profile {name!r} has duplicate task names")
        for task in tasks:
            unknown = set(task.depends_on) - set(names)
            if unknown:
                raise ValueError(f"task {task.name!r} depends on unknown tasks {sorted(unknown)}")
        _check_acyclic(name, tasks)
        profiles[str(name)] = Profile(str(name), tasks, str(raw.get("description", "")))
    return profiles


def _parse_task(profile: str, item: Any) -> TaskSpec:
    if not isinstance(item, Mapping) or not isinstance(item.get("name"), str) or not item["name"].strip():
        raise ValueError(f"every task in profile {profile!r} needs a name")
    command = item.get("command", [])
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list) or not all(isinstance(p, str) for p in command):
        raise ValueError(f"task {item['name']!r}: command must be a list of strings")
    url = item.get("url", "")
    if url and not str(url).startswith(("http://", "https://")):
        raise ValueError(f"task {item['name']!r}: url must be http(s)")
    if bool(command) == bool(url):
        raise ValueError(f"task {item['name']!r}: set exactly one of command or url")
    for key in ("detect", "ready", "stop"):
        probes = item.get(key, {})
        if not isinstance(probes, Mapping) or set(probes) - set(_PROBE_KINDS):
            raise ValueError(f"task {item['name']!r}: {key} probes must be among {_PROBE_KINDS}")
    window = item.get("window", "gui")
    if window not in _WINDOW_MODES:
        raise ValueError(f"task {item['name']!r}: window must be one of {_WINDOW_MODES}")
    timeout = item.get("timeout_seconds", 30.0)
    retries = item.get("retries", 1)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise ValueError(f"task {item['name']!r}: timeout_seconds must be within (0, 600]")
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
        raise ValueError(f"task {item['name']!r}: retries must be 0..5")
    return TaskSpec(
        name=item["name"].strip(),
        command=tuple(os.path.expandvars(p) for p in command),
        url=str(url),
        cwd=os.path.expandvars(str(item.get("cwd", ""))),
        depends_on=tuple(item.get("depends_on", [])),
        detect=dict(item.get("detect", {})),
        ready=dict(item.get("ready", {})),
        stop=dict(item.get("stop", {})),
        timeout_seconds=float(timeout),
        retries=retries,
        required=bool(item.get("required", False)),
        window=window,
    )


def _check_acyclic(profile: str, tasks: tuple[TaskSpec, ...]) -> None:
    deps = {t.name: set(t.depends_on) for t in tasks}
    while deps:
        free = [n for n, d in deps.items() if not d]
        if not free:
            raise ValueError(f"workspace profile {profile!r} has a dependency cycle: {sorted(deps)}")
        for name in free:
            deps.pop(name)
        for d in deps.values():
            d.difference_update(free)


# -- probes -----------------------------------------------------------------

def _probe_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _probe_port(target: Any) -> bool:
    host, _, port = str(target).rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=1.0):
            return True
    except (OSError, ValueError):
        return False


def _probe_process(name: str) -> bool:
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return False
    wanted = name.lower()
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == wanted:
            return True
    return False


def window_titles() -> list[tuple[int, str, int]]:
    """Visible top-level windows as (hwnd, title, pid); empty off Windows."""
    if not _IS_WINDOWS:
        return []
    import win32gui  # noqa: PLC0415
    import win32process  # noqa: PLC0415

    found: list[tuple[int, str, int]] = []

    def _collect(hwnd: int, _: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                found.append((hwnd, title, win32process.GetWindowThreadProcessId(hwnd)[1]))
        return True

    win32gui.EnumWindows(_collect, None)
    return found


def _probe_window(fragment: str) -> bool:
    fragment = fragment.lower()
    return any(fragment in title.lower() for _, title, _ in window_titles())


def _probe_command(argv: Any) -> bool:
    try:
        return subprocess.run(list(argv), capture_output=True, timeout=10, **_no_window()).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _no_window() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


DEFAULT_PROBES: dict[str, Callable[[Any], bool]] = {
    "http": _probe_http,
    "port": _probe_port,
    "process": _probe_process,
    "window_title": _probe_window,
    "command": _probe_command,
}


def resolve_executable(name: str) -> str:
    """Resolve argv[0]; ``code`` prefers Code.exe so no console window flashes."""
    if name.lower() == "code" and _IS_WINDOWS:
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
            for sub in (r"Programs\Microsoft VS Code\Code.exe", r"Microsoft VS Code\Code.exe"):
                candidate = Path(base) / sub
                if base and candidate.is_file():
                    return str(candidate)
    return shutil.which(name) or name


def default_spawn(task: TaskSpec, log_dir: Path) -> Any:
    argv = [resolve_executable(task.command[0]), *task.command[1:]]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "cwd": task.cwd or None}
    if task.window == "hidden":
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / f"{task.name}.log", "ab")  # noqa: SIM115 - handed to the child
        kwargs.update(stdout=log, stderr=subprocess.STDOUT)
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if _IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= {
            "hidden": subprocess.CREATE_NO_WINDOW,
            "new_console": subprocess.CREATE_NEW_CONSOLE,
            "gui": subprocess.DETACHED_PROCESS,
        }[task.window]
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def _process_identity(pid: int) -> Optional[float]:
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001 - gone, denied or psutil missing
        return None


def _terminate_tree(pid: int, create_time: Optional[float]) -> bool:
    """Terminate *pid* and its children, only if it is still the process we started."""
    try:
        import psutil  # noqa: PLC0415

        proc = psutil.Process(pid)
        if create_time is not None and abs(proc.create_time() - create_time) > 1.0:
            return False  # PID reused by an unrelated process
        family = proc.children(recursive=True) + [proc]
        for p in family:
            p.terminate()
        _, alive = psutil.wait_procs(family, timeout=5)
        for p in alive:
            p.kill()
        return True
    except Exception:  # noqa: BLE001 - already exited
        return False


def _close_windows(fragment: str) -> int:
    if not _IS_WINDOWS:
        return 0
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415

    closed = 0
    for hwnd, title, _ in window_titles():
        if fragment.lower() in title.lower():
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)  # the app may still ask to save
            closed += 1
    return closed


# -- manager ------------------------------------------------------------------

@dataclass
class TaskResult:
    name: str
    status: str  # already_running | launched | opened | failed | timeout | skipped
    elapsed_ms: float = 0.0
    detail: str = ""
    pid: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status in ("already_running", "launched", "opened")


class WorkspaceManager:
    def __init__(
        self,
        profiles: Mapping[str, Profile],
        *,
        state_path: Path,
        log_dir: Path,
        probes: Optional[Mapping[str, Callable[[Any], bool]]] = None,
        spawn: Callable[[TaskSpec, Path], Any] = default_spawn,
        open_url: Callable[[str], Any] = webbrowser.open,
        terminate: Callable[[int, Optional[float]], bool] = _terminate_tree,
        close_windows: Callable[[str], int] = _close_windows,
        identity: Callable[[int], Optional[float]] = _process_identity,
        poll_seconds: float = 0.25,
    ) -> None:
        self.profiles = dict(profiles)
        self._state_path = state_path
        self._log_dir = log_dir
        self._probes = dict(DEFAULT_PROBES, **(probes or {}))
        self._spawn = spawn
        self._open_url = open_url
        self._terminate = terminate
        self._close_windows = close_windows
        self._identity = identity
        self._poll = poll_seconds
        self._locks: dict[str, asyncio.Lock] = {}

    # -- probes ------------------------------------------------------------
    async def _check(self, probes: Mapping[str, Any]) -> bool:
        if not probes:
            return False
        for kind, target in probes.items():
            if await asyncio.to_thread(self._probes[kind], target):
                return True
        return False

    # -- start -------------------------------------------------------------
    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            raise ValueError(f"unknown workspace profile {name!r}; known: {sorted(self.profiles)}") from None

    async def start(self, name: str, only: Optional[set[str]] = None) -> list[TaskResult]:
        profile = self.profile(name)
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:  # a second "start" waits and then finds everything running
            tasks = [t for t in profile.tasks if only is None or t.name in only]
            done: dict[str, asyncio.Future[TaskResult]] = {
                t.name: asyncio.get_running_loop().create_future() for t in profile.tasks
            }
            for t in profile.tasks:
                if only is not None and t.name not in only:
                    done[t.name].set_result(TaskResult(t.name, "already_running", detail="not selected"))
            results = await asyncio.gather(*(self._run_task(name, t, done) for t in tasks))
            logger.info("workspace %s: %s", name, {r.name: r.status for r in results})
            return list(results)

    async def _run_task(self, profile: str, task: TaskSpec, done: dict[str, asyncio.Future]) -> TaskResult:
        started = time.monotonic()
        try:
            for dep in task.depends_on:
                dep_result = await done[dep]
                if not dep_result.ok:
                    result = TaskResult(task.name, "skipped", detail=f"dependency {dep} {dep_result.status}")
                    break
            else:
                result = await self._launch(profile, task)
        except Exception as error:  # noqa: BLE001 - one task never sinks the profile
            result = TaskResult(task.name, "failed", detail=str(error))
        result.elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if not done[task.name].done():
            done[task.name].set_result(result)
        return result

    async def _launch(self, profile: str, task: TaskSpec) -> TaskResult:
        if task.url:
            await asyncio.to_thread(self._open_url, task.url)
            return TaskResult(task.name, "opened")
        if await self._check(task.detect):
            return TaskResult(task.name, "already_running")
        last = TaskResult(task.name, "failed", detail="not attempted")
        for attempt in range(task.retries + 1):
            try:
                process = await asyncio.to_thread(self._spawn, task, self._log_dir)
            except OSError as error:
                last = TaskResult(task.name, "failed", detail=f"launch failed: {error}")
                break  # a missing executable will not appear on retry
            pid = getattr(process, "pid", None)
            if pid is not None:
                self._remember(profile, task, pid)
            last = await self._await_ready(task, process)
            last.pid = pid
            if last.ok:
                return last
            logger.warning("task %s attempt %d: %s %s", task.name, attempt + 1, last.status, last.detail)
            if attempt < task.retries:
                await asyncio.sleep(min(1.0 * (attempt + 1), 3.0))
        return last

    async def _await_ready(self, task: TaskSpec, process: Any) -> TaskResult:
        probes = task.ready or task.detect
        if not probes:
            return TaskResult(task.name, "launched")
        deadline = time.monotonic() + task.timeout_seconds
        while time.monotonic() < deadline:
            if await self._check(probes):
                return TaskResult(task.name, "launched")
            code = process.poll() if hasattr(process, "poll") else None
            # Launchers (code, wt) exit 0 immediately; only a non-zero exit is fatal.
            if code not in (None, 0):
                return TaskResult(task.name, "failed", detail=f"exited with code {code}")
            await asyncio.sleep(self._poll)
        return TaskResult(task.name, "timeout", detail=f"not ready after {task.timeout_seconds:.0f}s")

    # -- managed state -------------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _remember(self, profile: str, task: TaskSpec, pid: int) -> None:
        state = self._load_state()
        state.setdefault(profile, {})[task.name] = {"pid": pid, "create_time": self._identity(pid), "stop": dict(task.stop)}
        self._save_state(state)

    def managed(self, profile: str) -> dict[str, Any]:
        return dict(self._load_state().get(profile, {}))

    # -- stop -----------------------------------------------------------------
    async def stop(self, name: str, *, force: bool = False, only: Optional[set[str]] = None) -> list[TaskResult]:
        """Stop managed tasks in reverse dependency order.

        ``force`` (explicit owner authorization) also closes matching windows
        of tasks that were already running before Jarvis started them.
        """
        profile = self.profile(name)
        state = self._load_state()
        managed = state.get(name, {})
        results: list[TaskResult] = []
        for task in reversed(profile.tasks):
            if only is not None and task.name not in only:
                continue
            entry = managed.pop(task.name, None)
            if entry is None and not force:
                results.append(TaskResult(task.name, "skipped", detail="not managed by Jarvis"))
                continue
            stop = (entry or {}).get("stop") or dict(task.stop)
            if stop.get("window_title"):
                count = await asyncio.to_thread(self._close_windows, stop["window_title"])
                results.append(TaskResult(task.name, "stopped" if count else "skipped", detail=f"closed {count} window(s)"))
            elif entry is not None:
                ok = await asyncio.to_thread(self._terminate, entry["pid"], entry.get("create_time"))
                results.append(TaskResult(task.name, "stopped" if ok else "skipped", detail="" if ok else "already exited"))
            else:
                results.append(TaskResult(task.name, "skipped", detail="no stop rule"))
        state[name] = managed
        self._save_state(state)
        return results

    async def restart(self, name: str, task_name: str) -> list[TaskResult]:
        profile = self.profile(name)
        if task_name not in {t.name for t in profile.tasks}:
            raise ValueError(f"profile {name!r} has no task {task_name!r}")
        await self.stop(name, only={task_name})
        return await self.start(name, only={task_name})

    def find_task(self, task_name: str) -> Optional[tuple[str, TaskSpec]]:
        wanted = task_name.lower()
        for profile in self.profiles.values():
            for task in profile.tasks:
                if task.name.lower() == wanted:
                    return profile.name, task
        return None


def summarize(results: list[TaskResult]) -> str:
    """Short Spanish summary for speech."""
    launched = [r.name for r in results if r.status in ("launched", "opened")]
    running = [r.name for r in results if r.status == "already_running" and r.detail != "not selected"]
    failed = [
        r.name for r in results
        if r.status in ("failed", "timeout") or (r.status == "skipped" and "dependency" in r.detail)
    ]
    parts = []
    if launched:
        parts.append("He abierto " + ", ".join(launched))
    if running:
        parts.append("ya estaban en marcha " + ", ".join(running))
    if failed:
        parts.append("no he podido arrancar " + ", ".join(failed))
    return (". ".join(parts) + ".") if parts else "No había nada que hacer."
