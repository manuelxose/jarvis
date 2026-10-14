"""Real Windows window-state restore observer (one Electron launch per run). Import is inert; stdlib only.
Reuses the identity-checked Win32 helpers of the overlay probe; never launches or kills anything.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import importlib.util
import json
from pathlib import Path
import sys
import time

_spec = importlib.util.spec_from_file_location('overlay_native', Path(__file__).with_name('windows-overlay-probe.py'))
_native = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_native)
Win32, Blocked, poll, atomic_json, bounded_json = (_native.Win32, _native.Blocked, _native.poll, _native.atomic_json, _native.bounded_json)
PREFIX = 'desktop-native-window-state-'


class Monitor(C.Structure):
    _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]


def rect(r):
    return {'x': r.left, 'y': r.top, 'width': r.right - r.left, 'height': r.bottom - r.top}


class Probe:
    def __init__(self, root, directory, state):
        self.root, self.directory, self.state = root.resolve(), directory.resolve(), state.resolve()
        if self.root not in self.directory.parents or not self.directory.relative_to(self.root).parts[0].startswith(PREFIX) or directory.is_symlink():
            raise Blocked('run-path-escape')
        if self.state.parent != self.root or not self.state.name.startswith(PREFIX) or self.state.is_symlink():
            raise Blocked('state-path-escape')
        self.started, self.sequence, self.target, self.hwnds = time.monotonic(), 0, None, {}
        self.native = Win32()
        u = self.native.u
        atomic_json(self.directory / 'recipients.json', [])  # no third-party launches
        u.IsZoomed.argtypes = [W.HWND]; u.IsZoomed.restype = W.BOOL
        u.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]; u.MonitorFromWindow.restype = W.HANDLE
        u.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(Monitor)]; u.GetMonitorInfoW.restype = W.BOOL
        u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]; u.SetWindowPos.restype = W.BOOL
        u.EnumDisplayMonitors.argtypes = [W.HDC, C.c_void_p, C.c_void_p, W.LPARAM]; u.EnumDisplayMonitors.restype = W.BOOL

    def info(self, handle):
        info = Monitor(); info.size = C.sizeof(info)
        self.native.checked(self.native.u.GetMonitorInfoW(handle, C.byref(info)), 'monitor-info')
        return {'handle': str(int(handle or 0)), 'primary': bool(info.flags & 1), 'monitor': rect(info.monitor), 'workArea': rect(info.work)}

    def monitors(self):
        found = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM)
        def callback(handle, _dc, _rect, _param):
            found.append(self.info(handle)); return True
        cb = callback_type(callback)
        self.native.checked(self.native.u.EnumDisplayMonitors(None, None, C.cast(cb, C.c_void_p), 0), 'enum-monitors')
        return found

    def request(self, operation='observe'):
        if time.monotonic() - self.started > 85 and operation != 'quit':
            raise Blocked('session-budget')
        self.sequence += 1
        if self.sequence > 120:
            raise Blocked('request-budget')
        poll(lambda: self.state.is_dir(), 8)
        atomic_json(self.state / 'request.json', {'id': self.sequence, 'operation': operation})
        def response():
            try:
                f = self.state / 'response.json'
                value = bounded_json(f) if f.exists() else None
                return value if value and value.get('id') == self.sequence else None
            except (PermissionError, ValueError):
                return None
        return poll(response, 3 if operation == 'quit' else 15 if self.sequence == 1 else 5)

    def identify(self, reply):
        def read_journal():
            try:
                return bounded_json(self.directory / 'owned.json')
            except (PermissionError, ValueError):
                return None
        records = poll(read_journal, 5)
        records = records if isinstance(records, list) else [records]
        if not records or len(records) > 512:
            raise Blocked('ownership-journal-invalid')
        record = next((r for r in records if r['pid'] == reply['pid']), None)
        if not record:
            raise Blocked('unowned-main-process')
        target = {k: record[k] for k in ('pid', 'started', 'path')}
        if self.target is not None and target != self.target:
            raise Blocked('main-process-identity-changed')
        self.target = target

    def settle(self, host, mode, want_zoomed=None):
        """Poll until the host window is visible in `mode` with a stable native rect."""
        last = None
        def ready():
            nonlocal last
            reply = self.request()
            self.identify(reply)
            win = reply['windows'].get(host)
            if reply['mode'] != mode or not win or not win['visible']:
                return None
            hwnd = int(win['hwnd'])
            self.native.window(hwnd, self.target)
            if want_zoomed is not None and bool(self.native.u.IsZoomed(hwnd)) != want_zoomed:
                return None
            snapshot = (self.native.rect(hwnd), bool(self.native.u.IsZoomed(hwnd)))
            stable, last = last == snapshot, snapshot
            return (reply, hwnd) if stable else None
        reply, hwnd = poll(ready, 8)
        self.hwnds[host] = hwnd
        return reply, hwnd

    def observe(self, phase, host, mode, want_zoomed=None):
        reply, hwnd = self.settle(host, mode, want_zoomed)
        n = self.native
        info = self.info(n.u.MonitorFromWindow(hwnd, 2))
        return {'phase': phase, 'host': host, 'hwnd': str(hwnd), 'rect': n.rect(hwnd), 'monitorHandle': info['handle'],
                'workArea': info['workArea'], 'zoomed': bool(n.u.IsZoomed(hwnd)), 'mode': mode}

    def run(self, scenario, launch, report):
        cc = 'COMMAND_CENTER_WINDOWED'
        obs = report['observations']
        report['monitors'] = self.monitors()
        if scenario == 'overlay-position-restore':
            obs.append(self.observe('restored', 'overlay', 'OVERLAY_COMPACT'))
            self.request('mode:OVERLAY_EXPANDED')
            obs.append(self.observe('expanded', 'overlay', 'OVERLAY_EXPANDED'))
            return
        self.request('mode:' + cc)
        if scenario == 'persist-restore-secondary' and launch == 1:
            if len(report['monitors']) < 2:
                raise Blocked('single-monitor')
            _reply, hwnd = self.settle('command-center', cc)
            area = next(m for m in report['monitors'] if not m['primary'])['workArea']
            self.native.window(hwnd, self.target)
            self.native.checked(self.native.u.SetWindowPos(hwnd, None, area['x'] + 100, area['y'] + 100, 1000, 700, 0x14), 'native-move')
            time.sleep(0.5)
            obs.append(self.observe('saved', 'command-center', cc))
        elif scenario == 'maximized-restore' and launch == 1:
            self.settle('command-center', cc)
            self.request('maximize')
            obs.append(self.observe('saved', 'command-center', cc, True))
        elif scenario == 'maximized-restore':
            obs.append(self.observe('restored', 'command-center', cc, True))
        elif scenario == 'persist-restore-secondary' or scenario in ('offscreen-recovery', 'resolution-shrink-clamp'):
            obs.append(self.observe('restored', 'command-center', cc))
        else:
            raise Blocked('unknown-scenario')


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--preflight', action='store_true')
    p.add_argument('--root', type=Path); p.add_argument('--directory', type=Path); p.add_argument('--state', type=Path)
    p.add_argument('--scenario'); p.add_argument('--launch', type=int)
    args = p.parse_args(argv)
    if args.preflight:
        Win32(); print(json.dumps({'platform': sys.platform, 'ctypes': True})); return 0
    if not (args.root and args.directory and args.state and args.scenario and args.launch):
        p.error('root, directory, state, scenario and launch required')
    report = {'platform': sys.platform, 'preflight': {'ctypes': False}, 'status': 'BLOCKED', 'reason': 'not-observed', 'observations': [], 'monitors': []}
    probe = None
    try:
        probe = Probe(args.root, args.directory, args.state); report['preflight']['ctypes'] = True
        probe.run(args.scenario, args.launch, report)
        report['hwnd'] = str(next(iter(probe.hwnds.values()), 0)); report['status'] = 'PASS'; report.pop('reason')
    except Blocked as e:
        report['reason'] = str(e)
    except Exception as e:
        report.update(status='FAIL', reason=type(e).__name__ + ':' + str(e))
    finally:
        if probe:
            try: probe.request('quit')
            except Exception as e: report['quitError'] = str(e)
        atomic_json(args.directory / 'probe.json', report)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
