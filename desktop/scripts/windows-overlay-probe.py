"""Bounded real Win32 overlay acceptance. stdlib only; import has no native effects.

Controlled browser receipt is independent HTTP pointer acknowledgement. Explorer,
VS Code and terminal are isolated/owned launch attempts, but no invented UIA or
console receipt: unsupported controls are explicitly BLOCKED before any input.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import http.server
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time

APPS = ('explorer', 'vscode', 'browser', 'terminal')
ROUTES = ('empty-root', 'transparent-margin', 'exit-reentry', 'leave-reset', 'hide-reset')
REQUIRED = [f'{app}.{route}' for app in APPS for route in ROUTES] + [f'overlay.{name}' for name in ('control', 'mode-roundtrip', 'drag', 'native-surface', 'compositor')]
TICKS_OFFSET = 504911232000000000


class Blocked(Exception):
    pass


def bounded_json(file):
    if file.is_symlink() or file.stat().st_size > 4 * 1024 * 1024:
        raise Blocked('json-budget-or-symlink')
    return json.loads(file.read_text(encoding='utf-8-sig'))


def atomic_json(file, value):
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    # Windows readers (including Electron's state poll) can briefly hold a
    # non-delete-shared handle on UNC files. Keep the old valid journal until
    # replacement succeeds; never truncate it or retry without a bound.
    for attempt in range(6):
        try:
            os.replace(temporary, file)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05)


def poll(fn, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = fn()
        if result:
            return result
        time.sleep(.05)
    raise Blocked('observation-timeout')


class Win32:
    def __init__(self):
        if sys.platform != 'win32':
            raise Blocked('windows-python-required')
        self.u = C.WinDLL('user32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.g = C.WinDLL('gdi32', use_last_error=True)
        # Every pointer-sized argument/return is typed (64-bit HWNDs/handles).
        def bind(dll, name, result, *args):
            function = getattr(dll, name)
            function.restype, function.argtypes = result, list(args)
            return function
        bind(self.u, 'SetProcessDPIAware', W.BOOL)()
        bind(self.u, 'GetForegroundWindow', W.HWND)
        bind(self.u, 'IsWindow', W.BOOL, W.HWND)
        bind(self.u, 'IsWindowVisible', W.BOOL, W.HWND)
        bind(self.u, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
        bind(self.u, 'GetWindowRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
        bind(self.u, 'GetClientRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
        bind(self.u, 'ClientToScreen', W.BOOL, W.HWND, C.POINTER(W.POINT))
        bind(self.u, 'ScreenToClient', W.BOOL, W.HWND, C.POINTER(W.POINT))
        bind(self.u, 'GetCursorPos', W.BOOL, C.POINTER(W.POINT))
        bind(self.u, 'SetCursorPos', W.BOOL, C.c_int, C.c_int)
        bind(self.u, 'SetForegroundWindow', W.BOOL, W.HWND)
        bind(self.u, 'GetWindow', W.HWND, W.HWND, W.UINT)
        bind(self.u, 'GetAncestor', W.HWND, W.HWND, W.UINT)
        bind(self.u, 'WindowFromPoint', W.HWND, W.POINT)
        bind(self.u, 'GetWindowLongW', C.c_long, W.HWND, C.c_int)
        bind(self.u, 'SetWindowPos', W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
        self.enum_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        bind(self.u, 'EnumWindows', W.BOOL, self.enum_type, W.LPARAM)
        bind(self.k, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        bind(self.k, 'CloseHandle', W.BOOL, W.HANDLE)
        bind(self.k, 'GetProcessTimes', W.BOOL, W.HANDLE, *([C.POINTER(W.FILETIME)] * 4))
        bind(self.k, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
        bind(self.k, 'TerminateProcess', W.BOOL, W.HANDLE, W.UINT)
        bind(self.k, 'GetExitCodeProcess', W.BOOL, W.HANDLE, C.POINTER(W.DWORD))
        bind(self.k, 'CreateToolhelp32Snapshot', W.HANDLE, W.DWORD, W.DWORD)
        bind(self.u, 'GetDC', W.HDC, W.HWND)
        bind(self.u, 'ReleaseDC', C.c_int, W.HWND, W.HDC)
        bind(self.g, 'CreateCompatibleDC', W.HDC, W.HDC)
        bind(self.g, 'CreateCompatibleBitmap', W.HBITMAP, W.HDC, C.c_int, C.c_int)
        bind(self.g, 'SelectObject', W.HANDLE, W.HDC, W.HANDLE)
        bind(self.g, 'DeleteObject', W.BOOL, W.HANDLE)
        bind(self.g, 'DeleteDC', W.BOOL, W.HDC)
        bind(self.g, 'BitBlt', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD)
        bind(self.g, 'GetDIBits', C.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT, W.LPVOID, W.LPVOID, W.UINT)
        class Mouse(C.Structure):
            _fields_ = [('dx', C.c_long), ('dy', C.c_long), ('mouseData', W.DWORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]
        class Union(C.Union):
            _fields_ = [('mi', Mouse), ('padding', C.c_byte * (32 if C.sizeof(C.c_void_p) == 8 else 24))]
        class Input(C.Structure):
            _fields_ = [('type', W.DWORD), ('data', Union)]
        self.Mouse, self.Input = Mouse, Input
        bind(self.u, 'SendInput', W.UINT, W.UINT, C.POINTER(Input), C.c_int)
        class ProcessEntry(C.Structure):
            _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('pid', W.DWORD), ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD), ('parent', W.DWORD), ('priority', C.c_long), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]
        self.ProcessEntry = ProcessEntry
        bind(self.k, 'Process32FirstW', W.BOOL, W.HANDLE, C.POINTER(ProcessEntry))
        bind(self.k, 'Process32NextW', W.BOOL, W.HANDLE, C.POINTER(ProcessEntry))

    def checked(self, result, reason):
        if not result:
            raise Blocked(f'{reason}:win32-{C.get_last_error()}')
        return result

    def process(self, pid, handle=None):
        opened = handle is None
        handle = handle or self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            raise Blocked('process-access-denied-or-lost')
        try:
            exit_code = W.DWORD()
            self.checked(self.k.GetExitCodeProcess(handle, C.byref(exit_code)), 'process-exit-query')
            if exit_code.value != 259:
                raise Blocked('process-exited')
            values = [W.FILETIME() for _ in range(4)]
            self.checked(self.k.GetProcessTimes(handle, *[C.byref(v) for v in values]), 'process-start')
            size, name = W.DWORD(32768), C.create_unicode_buffer(32768)
            self.checked(self.k.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)), 'process-path')
            started = (values[0].dwHighDateTime << 32) + values[0].dwLowDateTime + TICKS_OFFSET
            return {'pid': int(pid), 'started': str(started), 'path': name.value}
        finally:
            if opened:
                self.k.CloseHandle(handle)

    def matches(self, record):
        try:
            return self.process(record['pid']) == record
        except Blocked:
            return False

    def hwnd_pid(self, hwnd):
        pid = W.DWORD()
        self.checked(self.u.GetWindowThreadProcessId(hwnd, C.byref(pid)), 'window-pid')
        return int(pid.value)

    def window(self, hwnd, record, visible=True):
        if not self.u.IsWindow(hwnd) or self.hwnd_pid(hwnd) != record['pid'] or not self.matches(record):
            raise Blocked('window-process-identity-changed')
        if visible and not self.u.IsWindowVisible(hwnd):
            raise Blocked('window-not-visible')

    def windows(self, records):
        found = []
        @self.enum_type
        def callback(hwnd, _):
            pid = W.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if self.u.IsWindowVisible(hwnd) and any(r['pid'] == pid.value and self.matches(r) for r in records):
                found.append(int(hwnd))
            return True
        self.checked(self.u.EnumWindows(callback, 0), 'enum-windows')
        return found

    def descendants(self, records, adopt=None):
        snapshot = self.k.CreateToolhelp32Snapshot(2, 0)
        if snapshot == C.c_void_p(-1).value:
            raise Blocked('process-inventory-denied')
        rows, entry = [], self.ProcessEntry()
        entry.dwSize = C.sizeof(entry)
        try:
            more = self.k.Process32FirstW(snapshot, C.byref(entry))
            while more:
                if len(rows) >= 4096:
                    raise Blocked('host-process-inventory-budget')
                rows.append((int(entry.pid), int(entry.parent)))
                more = self.k.Process32NextW(snapshot, C.byref(entry))
        finally:
            self.k.CloseHandle(snapshot)
        changed = True
        while changed:
            changed = False
            for pid, parent_pid in rows:
                if any(r['pid'] == pid for r in records):
                    continue
                parent = next((r for r in records if r['pid'] == parent_pid), None)
                if parent and self.matches(parent):
                    try:
                        child = self.process(pid)
                    except Blocked:
                        continue
                    # adopt=(pid, path): also own that image when its parent is that exact launch.
                    adopted = adopt is not None and parent['pid'] == adopt[0] and child['path'].lower() == adopt[1].lower()
                    if (adopted or child['path'].lower() == parent['path'].lower()) and int(child['started']) >= int(parent['started']):
                        if len(records) >= 512:
                            raise Blocked('owned-process-budget')
                        records.append(child)
                        changed = True

    def rect(self, hwnd, client=False):
        value = W.RECT()
        self.checked((self.u.GetClientRect if client else self.u.GetWindowRect)(hwnd, C.byref(value)), 'window-bounds')
        return {'x': value.left, 'y': value.top, 'width': value.right - value.left, 'height': value.bottom - value.top}

    def point(self, hwnd, x, y, to_screen=True):
        point = W.POINT(int(x), int(y))
        self.checked((self.u.ClientToScreen if to_screen else self.u.ScreenToClient)(hwnd, C.byref(point)), 'coordinate-conversion')
        return {'x': point.x, 'y': point.y}

    def focus(self, hwnd, record):
        self.window(hwnd, record)
        self.checked(self.u.SetForegroundWindow(hwnd), 'foreground-denied')
        poll(lambda: int(self.u.GetForegroundWindow() or 0) == hwnd, 4)
        self.window(hwnd, record)

    def move_window(self, hwnd, record, bounds):
        self.window(hwnd, record)
        self.checked(self.u.SetWindowPos(hwnd, None, bounds['x'], bounds['y'], bounds['width'], bounds['height'], 0x14), 'recipient-position')

    def diagnose(self, screen, allowed):
        """Evidence for a blocked input: foreground, window under the point and the stack there."""
        try:
            return ' fg=%s hit=%s allowed=%s stack=%s screen=%s' % (int(self.u.GetForegroundWindow() or 0), self.describe(self.hit(screen)), sorted(allowed), self.stack(screen), screen)
        except Exception:
            return ''

    def stack(self, screen):
        """Diagnostic only: visible top-level windows containing the point, top to bottom."""
        rows = []
        @self.enum_type
        def callback(hwnd, _):
            try:
                if self.u.IsWindowVisible(hwnd):
                    r = self.rect(hwnd)
                    if r['x'] <= screen['x'] < r['x'] + r['width'] and r['y'] <= screen['y'] < r['y'] + r['height']:
                        rows.append(self.describe(hwnd))
            except Exception:
                pass
            return True
        self.u.EnumWindows(callback, 0)
        return rows[:6]

    def cursor(self):
        point = W.POINT()
        self.u.GetCursorPos(C.byref(point))
        return (point.x, point.y)

    def describe(self, hwnd):
        """Diagnostic only: class, pid, image and rectangle of a window."""
        try:
            name = C.create_unicode_buffer(128)
            self.u.GetClassNameW(hwnd, name, 128)
            rect = self.rect(hwnd)
            title = C.create_unicode_buffer(80)
            self.u.GetWindowTextW(hwnd, title, 80)
            pid = self.hwnd_pid(hwnd)
            return '%s:%s:pid=%s:%s:%r:%s' % (hwnd, name.value, pid, rect, title.value, self.process(pid)['path'].split(chr(92))[-1])
        except Exception:
            return str(hwnd)

    def hit(self, screen):
        leaf = self.u.WindowFromPoint(W.POINT(screen['x'], screen['y']))
        return int(self.u.GetAncestor(leaf, 2) or leaf or 0)

    def input(self, hwnd, record, client, allowed, flags=(), move=True):
        """Revalidate recipient identity, focus, client rectangle and native hit immediately.
        No keyboard API; never clicks unowned desktop coordinates. Mouse up in finally
        is the only exception: release a held button on failure without another click.
        """
        self.window(hwnd, record)
        # Momentarily no foreground window at all is a transition, not another app taking focus.
        end = time.monotonic() + 1
        while not int(self.u.GetForegroundWindow() or 0) and time.monotonic() < end:
            time.sleep(.02)
        # A harness-owned window (the overlay after a click on it) holding focus is not theft: re-focus once.
        current = int(self.u.GetForegroundWindow() or 0)
        trusted = {r['pid'] for r in getattr(self, 'owned', [])} | {getattr(self, 'overlay_pid', None)}
        if current and current != hwnd and trusted - {None} and self.hwnd_pid(current) in trusted:
            self.focus(hwnd, record)
        if int(self.u.GetForegroundWindow() or 0) != hwnd:
            try:
                detail = ' fg=' + self.describe(int(self.u.GetForegroundWindow() or 0)) + ' want=' + self.describe(hwnd)
            except Exception:
                detail = ''
            raise Blocked('focus-stolen' + detail)
        bounds = self.rect(hwnd, True)
        x, y = client['x'], client['y']
        if type(x) is not int or type(y) is not int or not (0 <= x < bounds['width'] and 0 <= y < bounds['height']):
            raise Blocked('off-target-coordinates')
        screen = self.point(hwnd, x, y)
        under = self.hit(screen)
        # Before the move a click-through overlay is not hit-testable yet, so the
        # harness-owned recipient beneath it is expected; anything else is foreign.
        owned = {r['pid'] for r in getattr(self, 'owned', [])}
        if under not in allowed and not (move and under and owned and self.hwnd_pid(under) in owned):
            raise Blocked('foreign-window-over-input-point')
        if move:
            self.checked(self.u.SetCursorPos(screen['x'], screen['y']), 'cursor-position')
            # forwarded hit testing flips asynchronously; wait for the native hit, bounded
            waited = time.monotonic()
            end = waited + 2
            while self.hit(screen) not in allowed and time.monotonic() < end:
                time.sleep(.05)
            time.sleep(.12)  # let the overlay's cursor tracking settle before the identity re-check
        self.window(hwnd, record)
        if int(self.u.GetForegroundWindow() or 0) != hwnd or self.hit(screen) not in allowed:
            try:  # diagnostic: capture only an intruder owned by this harness
                intruder = self.hit(screen)
                if intruder and self.hwnd_pid(intruder) in {r['pid'] for r in getattr(self, 'owned', [])}:
                    self.screenshot(Path(__file__).resolve().parents[2] / ('intruder-%d.bmp' % intruder), self.rect(intruder))
            except Exception:
                pass
            raise Blocked('focus-or-hit-changed-before-input' + self.diagnose(screen, allowed))
        evidence = {'target': record, 'revalidated': self.process(record['pid']), 'hwnd': str(hwnd), 'foreground': str(self.u.GetForegroundWindow()), 'visible': True, 'hitValidated': True, 'clientBounds': bounds, 'client': client, 'screen': screen, 'flags': list(flags)}
        for flag in flags:
            self.window(hwnd, record)
            if int(self.u.GetForegroundWindow() or 0) != hwnd or self.hit(screen) not in allowed:
                raise Blocked('focus-or-hit-changed-during-input')
            packet = self.Input()
            packet.type = 0
            packet.data.mi = self.Mouse(0, 0, 0, flag, 0, 0)
            self.checked(self.u.SendInput(1, C.byref(packet), C.sizeof(packet)) == 1, 'input-denied')
        return evidence

    def release(self):
        packet = self.Input()
        packet.type = 0
        packet.data.mi = self.Mouse(0, 0, 0, 4, 0, 0)
        self.checked(self.u.SendInput(1, C.byref(packet), C.sizeof(packet)) == 1, 'release-denied')

    def screenshot(self, file, bounds):
        w, h = bounds['width'], bounds['height']
        if not (0 < w <= 1600 and 0 < h <= 1000):
            raise Blocked('screenshot-budget')
        screen = self.u.GetDC(None)
        dc = self.g.CreateCompatibleDC(screen)
        bitmap = self.g.CreateCompatibleBitmap(screen, w, h)
        old = self.g.SelectObject(dc, bitmap)
        try:
            self.checked(screen and dc and bitmap and old, 'screenshot-handles')
            self.checked(self.g.BitBlt(dc, 0, 0, w, h, screen, bounds['x'], bounds['y'], 0x40CC0020), 'screenshot-blit')
            self.g.SelectObject(dc, old)
            header = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
            info = C.create_string_buffer(header)
            pixels = C.create_string_buffer(w * h * 4)
            self.checked(self.g.GetDIBits(dc, bitmap, 0, h, pixels, info, 0) == h, 'screenshot-pixels')
            file.write_bytes(struct.pack('<2sIHHI', b'BM', 54 + len(pixels.raw), 0, 0, 54) + header + pixels.raw)
            return pixels.raw
        finally:
            if old:
                self.g.SelectObject(dc, old)
            if bitmap:
                self.g.DeleteObject(bitmap)
            if dc:
                self.g.DeleteDC(dc)
            if screen:
                self.u.ReleaseDC(None, screen)

    def cleanup(self, records):
        errors = []
        for record in reversed(records):
            # Hold the exact process handle throughout revalidation/termination.
            handle = self.k.OpenProcess(0x1001, False, record['pid'])
            if not handle:
                if C.get_last_error() != 87:  # ERROR_INVALID_PARAMETER = absent PID
                    errors.append(f'cleanup-access-denied-or-unobservable:{record["pid"]}')
                continue
            try:
                current = self.process(record['pid'], handle)
                if current == record:
                    self.checked(self.k.TerminateProcess(handle, 0), 'cleanup-terminate')
            except Blocked as error:
                if str(error) != 'process-exited':
                    errors.append(str(error))
            finally:
                self.k.CloseHandle(handle)
        end = time.monotonic() + 3
        remaining = [r for r in records if self.matches(r)]
        while remaining and time.monotonic() < end:
            time.sleep(.05)
            remaining = [r for r in records if self.matches(r)]
        return {'remaining': remaining, 'errors': errors}


class UIA:
    """Windows built-in UIAutomation COM, read-only properties plus focus reset.

    No PowerShell Add-Type, dependencies, text injection or InvokePattern. A
    native focus marker must change after SendInput, otherwise receipt is BLOCKED.
    COM provider stalls remain bounded by the parent session watchdog.
    """
    def __init__(self):
        class GUID(C.Structure):
            _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', C.c_ubyte * 8)]
        class Value(C.Union):
            _fields_ = [('number', C.c_long), ('boolean', C.c_short), ('pointer', C.c_void_p), ('padding', C.c_byte * (16 if C.sizeof(C.c_void_p) == 8 else 8))]
        class Variant(C.Structure):
            _fields_ = [('vt', W.WORD), ('r1', W.WORD), ('r2', W.WORD), ('r3', W.WORD), ('value', Value)]
        self.Variant = Variant
        self.ole, self.auto = C.OleDLL('ole32'), C.OleDLL('oleaut32')
        self.refs = []
        self.ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
        self.ole.CoInitializeEx(None, 0)
        self.ole.CLSIDFromString.argtypes = [W.LPCWSTR, C.POINTER(GUID)]
        self.ole.CoCreateInstance.argtypes = [C.POINTER(GUID), C.c_void_p, W.DWORD, C.POINTER(GUID), C.POINTER(C.c_void_p)]
        clsid, iid = GUID(), GUID()
        self.ole.CLSIDFromString('{FF48DBA4-60EF-4201-AA87-54103EEF594E}', C.byref(clsid))
        self.ole.CLSIDFromString('{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}', C.byref(iid))
        self.client = C.c_void_p()
        self.ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(self.client))
        self.refs.append(self.client)
        self.auto.VariantClear.argtypes = [C.POINTER(Variant)]
        self.auto.SafeArrayGetLBound.argtypes = [C.c_void_p, W.UINT, C.POINTER(C.c_long)]
        self.auto.SafeArrayGetUBound.argtypes = [C.c_void_p, W.UINT, C.POINTER(C.c_long)]
        self.auto.SafeArrayAccessData.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
        self.auto.SafeArrayUnaccessData.argtypes = [C.c_void_p]

    def call(self, obj, index, *args):
        if not obj:
            raise Blocked('uia-null-interface')
        table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
        types, values = zip(*args) if args else ((), ())
        method = C.WINFUNCTYPE(C.c_long, C.c_void_p, *types)(table[index])
        result = method(obj, *values)
        if result < 0:
            raise Blocked(f'uia-provider-error:{result}')
        return result

    def property(self, element, property_id):
        variant = self.Variant()
        self.call(element, 10, (C.c_int, property_id), (C.POINTER(self.Variant), C.byref(variant)))
        try:
            if variant.vt == 3:
                return int(variant.value.number)
            if variant.vt == 11:
                return bool(variant.value.boolean)
            if variant.vt == 8:
                return C.wstring_at(variant.value.pointer) if variant.value.pointer else ''
            if variant.vt == 8197:  # VT_ARRAY | VT_R8: bounding rectangle
                array = variant.value.pointer
                if not array:
                    raise Blocked('uia-invalid-bounds')
                low, high, data = C.c_long(), C.c_long(), C.c_void_p()
                self.auto.SafeArrayGetLBound(array, 1, C.byref(low))
                self.auto.SafeArrayGetUBound(array, 1, C.byref(high))
                if high.value - low.value != 3:
                    raise Blocked('uia-invalid-bounds')
                self.auto.SafeArrayAccessData(array, C.byref(data))
                try:
                    if not data:
                        raise Blocked('uia-invalid-bounds')
                    values = list(C.cast(data, C.POINTER(C.c_double * 4)).contents)
                    if not all(math.isfinite(v) for v in values):
                        raise Blocked('uia-invalid-bounds')
                    return dict(zip(('x', 'y', 'width', 'height'), values))
                finally:
                    self.auto.SafeArrayUnaccessData(array)
            raise Blocked('uia-property-unavailable:' + str(property_id))
        finally:
            self.auto.VariantClear(C.byref(variant))

    def controls(self, hwnd, records):
        element, condition, array = C.c_void_p(), C.c_void_p(), C.c_void_p()
        self.call(self.client, 6, (W.HWND, hwnd), (C.POINTER(C.c_void_p), C.byref(element)))
        self.refs.append(element)
        self.call(self.client, 21, (C.POINTER(C.c_void_p), C.byref(condition)))
        self.refs.append(condition)
        self.call(element, 6, (C.c_int, 4), (C.c_void_p, condition), (C.POINTER(C.c_void_p), C.byref(array)))
        self.refs.append(array)
        length = C.c_int()
        self.call(array, 3, (C.POINTER(C.c_int), C.byref(length)))
        if not 0 < length.value <= 512:
            raise Blocked('uia-control-inventory-budget')
        result = []
        for index in range(length.value):
            control = C.c_void_p()
            self.call(array, 4, (C.c_int, index), (C.POINTER(C.c_void_p), C.byref(control)))
            self.refs.append(control)
            try:
                if not any(r['pid'] == self.property(control, 30002) for r in records) or self.property(control, 30022) or not self.property(control, 30010) or not self.property(control, 30009) or self.property(control, 30019):
                    continue
                bounds = self.property(control, 30001)
                if bounds['width'] > 4 and bounds['height'] > 4:
                    result.append({'element': control, 'bounds': bounds, 'type': self.property(control, 30003), 'name': self.property(control, 30005)})
            except Blocked:
                continue
        return result

    def focus(self, element):
        self.call(element, 3)

    def close(self):
        for obj in reversed(self.refs):
            if obj:
                self.call(obj, 2)
        self.refs = []
        self.ole.CoUninitialize()


class Page:
    """Disposable loopback browser page, independent click receipt, no user content."""
    def __init__(self):
        self.receipts, self.ready, self.control = [], None, None
        self.token = secrets.token_hex(16)
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path != '/' + owner.token:
                    self.send_error(404)
                    return
                body = ('<!doctype html><meta charset="utf-8"><title>Jarvis disposable input recipient</title>'
                        '<style>html,body{margin:0;width:100%;height:100%;background:#43816e}button{position:absolute;inset:0;width:100%;height:100%;background:#43816e;border:0;color:white}</style>'
                        '<button id="recipient">Disposable recipient · no user data</button><script>'
                        'const post=(kind,e)=>fetch(location.pathname,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind,x:Math.round(e?.clientX||0),y:Math.round(e?.clientY||0),pointerScreenX:Math.round(e?.screenX||0),pointerScreenY:Math.round(e?.screenY||0),width:innerWidth,height:innerHeight,screenX,screenY,outerWidth,outerHeight,dpr:devicePixelRatio})});'
                        'post("ready");addEventListener("resize",()=>post("ready"));recipient.addEventListener("pointerdown",e=>{if(e.isTrusted)post("pointer",e)});</script>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                try:
                    if self.path != '/' + owner.token or len(owner.receipts) >= 32:
                        raise ValueError()
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 2048:
                        raise ValueError()
                    value = json.loads(self.rfile.read(length))
                    keys = ('x', 'y', 'pointerScreenX', 'pointerScreenY', 'width', 'height', 'screenX', 'screenY', 'outerWidth', 'outerHeight')
                    if value.get('kind') not in ('ready', 'pointer') or value.get('dpr') != 1 or any(type(value.get(k)) is not int or abs(value[k]) > 100000 for k in keys) or value['width'] <= 0 or value['height'] <= 0:
                        raise ValueError()
                    if value['kind'] == 'ready':
                        owner.ready = value
                    else:
                        owner.receipts.append(value)
                    self.send_response(204)
                    self.end_headers()
                except (ValueError, KeyError, json.JSONDecodeError):
                    self.send_error(400)
        self.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = .5
        self.server.RequestHandlerClass.timeout = .5
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/{self.token}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


class Acceptance:
    def __init__(self, root, directory, win):
        self.root, self.directory, self.win = root, directory, win
        self.started = time.monotonic()
        self.owned, self.request_id, self.phase, self.shots = [], 0, 0, 0
        self.win.owned = self.owned
        self.inputs = 0
        self.disposable = []
        self.report = {'platform': sys.platform, 'realInput': True, 'preflight': {'ctypes': True}, 'scenarios': [{'id': name, 'status': 'BLOCKED', 'reason': 'not-executed'} for name in REQUIRED], 'cleanup': {'remaining': [], 'errors': []}}
        self.page = None
        self.original_foreground = int(win.u.GetForegroundWindow() or 0)
        try:
            self.original_record = win.process(win.hwnd_pid(self.original_foreground))
        except Blocked:
            self.original_record = None
        self.original_cursor = W.POINT()
        self.cursor_saved = bool(win.u.GetCursorPos(C.byref(self.original_cursor)))
        atomic_json(directory / 'recipients.json', self.owned)

    def budget(self):
        if time.monotonic() - self.started > 85 or self.inputs >= 32 or self.shots >= 32:
            raise Blocked('session-budget')

    def save(self):
        atomic_json(self.directory / 'probe.json', self.report)

    def result(self, name, **value):
        row = next(s for s in self.report['scenarios'] if s['id'] == name)
        row.update(value)
        if value.get('status') == 'PASS':
            row.pop('reason', None)
        self.save()

    def request(self, operation='observe'):
        if operation not in ('show', 'quit'):  # cleanup may run after session budget
            self.budget()
        self.request_id += 1
        state = self.directory / 'state'
        poll(lambda: state.is_dir(), 8)
        atomic_json(state / 'request.json', {'id': self.request_id, 'operation': operation})
        def response():
            file = state / 'response.json'
            try:
                if not file.exists():
                    return None
                value = bounded_json(file)
            except PermissionError:  # the controller is replacing the file on the UNC share
                return None
            return value if value.get('id') == self.request_id else None
        # Cold mount over the WSL UNC share can exceed a normal observation.
        # Keep later reads short and preserve the overall session deadline.
        value = poll(response, min(15 if self.request_id == 1 else 3, max(0, self.started + 85 - time.monotonic())))
        if operation == 'hide':
            self.phase = 1
        elif operation == 'show':
            self.phase = 0
        return value

    def overlay(self):
        value = self.request()
        self.win.overlay_pid = int(value['pid'])
        hwnd = int(value['hwnd'])
        record = self.win.process(value['pid'])
        journal = bounded_json(self.directory / 'owned.json')
        journal = journal if isinstance(journal, list) else [journal]
        if not any(r['pid'] == record['pid'] and r['started'] == record['started'] and r['path'].lower() == record['path'].lower() for r in journal):
            raise Blocked('overlay-not-in-host-ownership-journal')
        self.win.window(hwnd, record, value['visible'])
        native_bounds = self.win.rect(hwnd, True)
        if value.get('zoom') != 1 or native_bounds['width'] != value['bounds']['width'] or native_bounds['height'] != value['bounds']['height']:
            raise Blocked('overlay-dpi-or-zoom-not-supported')
        value['target'] = record
        return value

    def launch(self, app, exe):
        self.budget()
        if not exe or not Path(exe).is_file():
            raise Blocked(f'{app}-application-missing')
        area = self.directory / ('disposable-' + app)
        area.mkdir()
        self.disposable.append(area)
        atomic_json(area / '.harness-owner.json', {'run': self.directory.name, 'app': app})
        if app == 'browser':
            self.page = Page()
            args = [exe, '--user-data-dir=' + str(area), '--no-first-run', '--no-default-browser-check', '--force-device-scale-factor=1', '--disable-sync', '--disable-features=msImplicitSignin,msEdgeSyncConsent,msEdgeFirstRunExperience', '--disable-extensions', '--app=' + self.page.url]
        elif app == 'vscode':
            settings = area / 'profile' / 'User'
            settings.mkdir(parents=True)
            atomic_json(settings / 'settings.json', {'workbench.startupEditor': 'newUntitledFile', 'accessibility.support': 'on', 'window.restoreWindows': 'none'})
            args = [exe, '--new-window', '--disable-extensions', '--force-renderer-accessibility', '--user-data-dir=' + str(area / 'profile'), '--extensions-dir=' + str(area / 'extensions')]
        elif app == 'explorer':
            (area / 'disposable-selection-marker.txt').write_text('Harmless harness-owned marker', encoding='utf-8')
            args = [exe, '/separate,', '/n,', str(area)]
        else:
            # Windows 11 hands a bare cmd.exe console to Windows Terminal, which this run
            # does not own; an explicit conhost keeps the console window in our process tree.
            args = [str(Path(exe).with_name('conhost.exe')), exe, '/D', '/Q']  # no command /C, /K or keyboard input
        # conhost forwards redirected std handles to cmd, which then reads EOF and exits: give it none.
        std = {} if app == 'terminal' else {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        process = subprocess.Popen(args, cwd=area, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | (subprocess.CREATE_NEW_CONSOLE if app == 'terminal' else 0), **std)
        record = self.win.process(process.pid)
        if Path(record['path']).resolve() != Path(args[0]).resolve():
            raise Blocked('recipient-launch-path-mismatch')
        self.owned.append(record)
        atomic_json(self.directory / 'recipients.json', self.owned)
        def find():
            self.win.descendants(self.owned, (record['pid'], exe) if app == 'terminal' else None)
            atomic_json(self.directory / 'recipients.json', self.owned)
            # Only same-image descendants of this owned launch, never an existing
            # Explorer/Code/browser window which absorbed a shell handoff.
            records = [r for r in self.owned if r['path'].lower() == exe.lower() and int(r['started']) >= int(record['started'])]
            windows = self.win.windows(records)
            # An app can own helper/popup top-level windows; the recipient is its largest one.
            return max(windows, key=lambda h: (lambda r: r['width'] * r['height'])(self.win.rect(h))) if windows else None
        try:
            hwnd = poll(find, 20 if app == 'vscode' else 6)  # VS Code cold start
        except Blocked:
            # Exited launch = shell handoff to a process we do not own; alive = no visible window.
            raise Blocked(f'{app}-window-not-observed' if self.win.matches(record) else f'{app}-launch-exited-before-window')
        target = self.win.process(self.win.hwnd_pid(hwnd))
        self.win.focus(hwnd, target)
        return hwnd, target

    def shot(self, overlay, hwnd, record):
        self.budget()
        self.win.window(hwnd, record)
        self.win.window(int(overlay['hwnd']), overlay['target'], overlay['visible'])
        bounds = self.win.rect(int(overlay['hwnd']))
        origin = self.win.point(hwnd, 0, 0)
        client = self.win.rect(hwnd, True)
        # Never capture user desktop/documents outside the dedicated browser.
        if not (origin['x'] <= bounds['x'] and origin['y'] <= bounds['y'] and bounds['x'] + bounds['width'] <= origin['x'] + client['width'] and bounds['y'] + bounds['height'] <= origin['y'] + client['height']):
            raise Blocked('screenshot-outside-disposable-recipient')
        # Refuse foreign windows intersecting the capture above our recipient.
        cursor = hwnd
        for _ in range(512):
            cursor = self.win.u.GetWindow(cursor, 3)
            if not cursor:
                break
            if int(cursor) == int(overlay['hwnd']) or not self.win.u.IsWindowVisible(cursor):
                continue
            other = self.win.rect(cursor)
            if other['x'] < bounds['x'] + bounds['width'] and other['x'] + other['width'] > bounds['x'] and other['y'] < bounds['y'] + bounds['height'] and other['y'] + other['height'] > bounds['y']:
                raise Blocked('foreign-window-over-screenshot')
        else:
            raise Blocked('screenshot-z-order-budget')
        self.shots += 1
        file = self.directory / f'screenshot-{self.shots:02}.bmp'
        pixels = self.win.screenshot(file, bounds)
        return str(file.relative_to(self.root)).replace('\\', '/'), pixels, bounds

    def click(self, hwnd, target, point, allowed):
        self.budget()
        self.inputs += 1
        down = False
        try:
            evidence = self.win.input(hwnd, target, point, allowed, (2,))
            down = True
            self.win.input(hwnd, target, point, allowed, (4,), move=False)
            down = False
            return evidence
        finally:
            if down:
                self.win.release()

    def center(self, overlay, element):
        rect = overlay['geometry'].get(element)
        if not rect or not all(type(rect.get(k)) in (float, int) and math.isfinite(rect[k]) for k in ('x', 'y', 'width', 'height')) or rect['width'] <= 0 or rect['height'] <= 0:
            raise Blocked('visible-overlay-control-missing')
        return {'x': int(rect['x'] + rect['width'] / 2), 'y': int(rect['y'] + rect['height'] / 2)}

    def empty(self, overlay, margin=False):
        card = overlay['geometry'].get('card')
        bounds = self.win.rect(int(overlay['hwnd']), True)
        if not card or bounds['height'] <= card['y'] + card['height'] + 4:
            raise Blocked('visible-empty-overlay-region-missing')
        return {'x': 2 if margin else bounds['width'] // 2, 'y': bounds['height'] - 3}

    def agree(self, overlay, expanded):
        return overlay['mode'] == ('OVERLAY_EXPANDED' if expanded else 'OVERLAY_COMPACT') and overlay['geometry'].get('label') == ('Collapse' if expanded else 'Expand') and overlay['bounds']['height'] == (520 if expanded else 160)

    def native_recipient(self, app, hwnd, target):
        """UIA independently observes a safe control focus change after OS input.

        Explorer: only our disposable marker. VS Code: isolated untitled edit.
        Terminal: only an owned document buffer, never a command sent to it.
        Missing UIA controls/reset capability is BLOCKED, not HWND-based PASS.
        """
        self.win.move_window(hwnd, target, {'x': 0, 'y': 0, 'width': 1400, 'height': 850})
        for route in ROUTES:
            uia = UIA()
            try:
                current = self.overlay()
                native = int(current['hwnd'])
                self.win.focus(hwnd, target)
                controls = uia.controls(hwnd, [r for r in self.owned if self.win.matches(r)])
                choices = [c for c in controls if (app == 'explorer' and c['type'] == 50007 and c['name'].startswith('disposable-selection-marker')) or (app == 'vscode' and c['type'] in (50004, 50030)) or (app == 'terminal' and c['type'] == 50030)]
                if not choices:
                    raise Blocked(app + '-visible-safe-uia-control-unavailable')
                control = choices[0]
                resets = [c for c in controls if c['element'].value != control['element'].value]
                if not resets:
                    raise Blocked(app + '-independent-focus-reset-unavailable')
                point = self.empty(current, route == 'transparent-margin')
                desired = self.win.point(native, point['x'], point['y'])
                bounds = control['bounds']
                rect = self.win.rect(hwnd)
                # Align a point strictly inside the observed safe control below
                # the overlay empty region; do not guess app screen coordinates.
                rect['x'] += desired['x'] - int(bounds['x'] + 8)
                rect['y'] += desired['y'] - int(bounds['y'] + 8)
                self.win.move_window(hwnd, target, rect)
                time.sleep(.2)
                bounds = uia.property(control['element'], 30001)
                if not (bounds['x'] < desired['x'] < bounds['x'] + bounds['width'] and bounds['y'] < desired['y'] < bounds['y'] + bounds['height']):
                    raise Blocked('uia-control-moved-outside-input')
                if route == 'exit-reentry':
                    self.win.focus(native, current['target'])
                    self.win.input(native, current['target'], self.center(current, 'toggle'), {native})
                self.win.focus(hwnd, target)
                if route == 'leave-reset':
                    self.win.input(hwnd, target, {'x': self.win.rect(hwnd, True)['width'] - 10, 'y': self.win.rect(hwnd, True)['height'] - 10}, {hwnd})
                if route == 'hide-reset':
                    self.request('hide')
                    current = self.overlay()
                    if current['visible']:
                        raise Blocked('hide-not-observed')
                uia.focus(resets[0]['element'])  # reversible setup, NOT receipt
                try:
                    poll(lambda: not uia.property(control['element'], 30008), 1)
                except Blocked:
                    raise Blocked(app + '-focus-reset-not-observed')  # e.g. a console's single text area keeps focus
                self.win.window(hwnd, target)
                # Re-read control visibility/PID/bounds immediately before input.
                if uia.property(control['element'], 30022) or uia.property(control['element'], 30002) not in [r['pid'] for r in self.owned if self.win.matches(r)]:
                    raise Blocked('uia-control-identity-or-visibility-lost')
                latest = uia.property(control['element'], 30001)
                if not (latest['x'] < desired['x'] < latest['x'] + latest['width'] and latest['y'] < desired['y'] < latest['y'] + latest['height']):
                    raise Blocked('uia-input-outside-visible-control')
                client = self.win.point(hwnd, desired['x'], desired['y'], False)
                evidence = self.click(hwnd, target, client, {hwnd, native})
                poll(lambda: uia.property(control['element'], 30008), 2)
                shot, _, _ = self.shot(current, hwnd, target)
                self.result(app + '.' + route, status='PASS', target=target, hwnd=str(hwnd), inputs=[evidence], screenshots=[shot], receipt={'kind': 'uia-focus', 'before': 0, 'after': 1, 'correlated': True, 'controlType': control['type'], 'visibleControlBounds': latest, 'property': 'HasKeyboardFocus'})
                if route == 'hide-reset':
                    self.request('show')
            finally:
                uia.close()

    def browser(self, hwnd, target):
        self.win.focus(hwnd, target)
        poll(lambda: self.page.ready, 15)
        overlay = self.overlay()
        rect = self.win.rect(int(overlay['hwnd']))
        self.win.move_window(hwnd, target, {'x': max(0, rect['x'] - 80), 'y': max(0, rect['y'] - 80), 'width': max(800, rect['width'] + 240), 'height': 800})
        self.win.focus(hwnd, target)
        overlay = self.overlay()
        native = int(overlay['hwnd'])
        def receipt(route, margin=False):
            current = self.overlay()
            self.win.focus(hwnd, target)
            point = self.empty(current, margin)
            screen = self.win.point(native, point['x'], point['y'])
            client = self.win.point(hwnd, screen['x'], screen['y'], False)
            # The page's full-client button is the explicit visible control.
            ready = self.page.ready
            body_y = ready['outerHeight'] - ready['height']
            if not (0 <= client['x'] < ready['width'] and body_y <= client['y'] < self.win.rect(hwnd, True)['height']):
                raise Blocked('input-outside-visible-browser-button')
            before = len(self.page.receipts)
            evidence = self.click(hwnd, target, client, {hwnd, native})
            poll(lambda: len(self.page.receipts) > before, 2)
            observed = self.page.receipts[before:]
            # Browser app windows have a titlebar/non-client offset; bound the
            # acknowledgement to this click's viewport point, not any event.
            correlated = len(observed) == 1 and observed[0]['pointerScreenX'] == screen['x'] and observed[0]['pointerScreenY'] == screen['y']
            shot, _, _ = self.shot(current, hwnd, target)
            self.result('browser.' + route, status='PASS' if correlated else 'FAIL', reason='recipient-coordinate-mismatch', target=target, hwnd=str(hwnd), inputs=[evidence], screenshots=[shot], receipt={'kind': 'controlled-page-pointer', 'before': before, 'after': len(self.page.receipts), 'correlated': correlated, 'observed': observed})
        receipt('empty-root')
        receipt('transparent-margin', True)
        # Positive controls: real Windows input and independent native/renderer state.
        controls = []
        for expanded in (True, False):
            current = self.overlay()
            self.win.focus(native, current['target'])
            controls.append(self.click(native, current['target'], self.center(current, 'toggle'), {native}))
            current = poll(lambda: (value if self.agree(value, expanded) else None) if (value := self.overlay()) else None, 8)
        shot, _, _ = self.shot(current, hwnd, target)
        for name in ('control', 'mode-roundtrip'):
            self.result('overlay.' + name, status='PASS', target=current['target'], hwnd=str(native), inputs=controls, screenshots=[shot], observation={'rendererNativeAgree': True, 'roundtrip': True})
        # Exit/reentry into explicit interactive surface; no arbitrary key/text.
        self.win.focus(native, current['target'])
        point = self.center(current, 'toggle')
        self.win.input(native, current['target'], point, {native})
        receipt('exit-reentry')
        # Real move outside the overlay (but inside owned recipient) then reentry.
        self.win.focus(hwnd, target)
        self.win.input(hwnd, target, {'x': 20, 'y': self.win.rect(hwnd, True)['height'] - 20}, {hwnd})
        receipt('leave-reset')
        self.drag(current, hwnd, target)
        current = self.overlay()
        style = int(self.win.u.GetWindowLongW(native, -16)) & 0xffffffff
        exstyle = int(self.win.u.GetWindowLongW(native, -20)) & 0xffffffff
        above, cursor = False, hwnd
        for _ in range(512):
            cursor = self.win.u.GetWindow(cursor, 3)  # GW_HWNDPREV
            if not cursor:
                break
            if int(cursor) == native:
                above = True
                break
        shot, shown, bounds = self.shot(current, hwnd, target)
        facts = {'style': style, 'extendedStyle': exstyle, 'borderless': not bool(style & 0x00C00000), 'topmost': bool(exstyle & 8), 'aboveRecipient': above}
        self.result('overlay.native-surface', status='PASS' if all(facts[k] for k in ('borderless', 'topmost', 'aboveRecipient')) else 'FAIL', reason='native-style-or-order-mismatch', target=current['target'], hwnd=str(native), inputs=[], screenshots=[shot], observation=facts)
        self.request('hide')
        hidden = self.overlay()
        if hidden['visible']:
            raise Blocked('hide-not-observed')
        shot_hidden, hidden_pixels, _ = self.shot(hidden, hwnd, target)
        card = self.center(current, 'card')
        margin = self.empty(current, True)
        def pixel(data, point):
            offset = (point['y'] * bounds['width'] + point['x']) * 4
            return data[offset:offset + 3]
        compositor = {'marginEqual': pixel(shown, margin) == pixel(hidden_pixels, margin), 'cardDifferent': pixel(shown, card) != pixel(hidden_pixels, card)}
        self.result('overlay.compositor', status='PASS' if all(compositor.values()) else 'FAIL', reason='compositor-pixel-mismatch', target=current['target'], hwnd=str(native), inputs=[], screenshots=[shot, shot_hidden], observation=compositor)
        receipt('hide-reset')
        self.request('show')

    def drag(self, current, hwnd, target):
        native = int(current['hwnd'])
        self.win.focus(native, current['target'])
        before = self.win.rect(native)
        start = self.center(current, 'drag')
        held, inputs = False, []
        try:
            self.budget()
            self.inputs += 1
            inputs.append(self.win.input(native, current['target'], start, {native}, (2,)))
            held = True
            for step in range(1, 4):
                # Track screen displacement while the overlay itself moves.
                screen = self.win.point(native, start['x'], start['y']) if step == 1 else initial
                if step == 1:
                    initial = screen
                point = self.win.point(native, initial['x'] + step * 8, initial['y'] + step * 4, False)
                inputs.append(self.win.input(native, current['target'], point, {native}))
            self.win.release()
            held = False
        finally:
            if held:
                self.win.release()
        after = self.win.rect(native)
        dx, dy = after['x'] - before['x'], after['y'] - before['y']
        shot, _, _ = self.shot(self.overlay(), hwnd, target)
        self.result('overlay.drag', status='PASS' if abs(dx) + abs(dy) >= 8 else 'FAIL', reason='native-drag-no-displacement', target=current['target'], hwnd=str(native), inputs=inputs, screenshots=[shot], observation={'before': before, 'after': after, 'dx': dx, 'dy': dy})

    def retire(self):
        """Terminate the app just exercised so a slow starter (VS Code) cannot
        surface later over the next recipient. Only exact-identity owned records."""
        try:
            self.win.descendants(self.owned)
            atomic_json(self.directory / 'recipients.json', self.owned)
            self.report['cleanup']['errors'] += self.win.cleanup(self.owned)['errors']
        except (Blocked, OSError) as error:
            self.report['cleanup']['errors'].append('retire:' + str(error))

    def run(self):
        try:
            apps = bounded_json(self.directory / 'apps.json')
            for app in APPS:
                try:
                    hwnd, target = self.launch(app, apps.get(app))
                    if app == 'browser':
                        self.browser(hwnd, target)
                    else:
                        self.native_recipient(app, hwnd, target)
                except (Blocked, OSError, ValueError, KeyError) as error:
                    for route in ROUTES:
                        name = app + '.' + route
                        row = next(s for s in self.report['scenarios'] if s['id'] == name)
                        if row['status'] == 'BLOCKED':
                            self.result(name, status='BLOCKED', reason=str(error))
                    if app == 'browser':
                        for name in REQUIRED:
                            if name.startswith('overlay.') and next(s for s in self.report['scenarios'] if s['id'] == name)['status'] == 'BLOCKED':
                                self.result(name, status='BLOCKED', reason=str(error))
                    if any(reason in str(error) for reason in ('focus-stolen', 'focus-or-hit-changed', 'foreign-window', 'identity-changed', 'session-budget')):
                        self.report['interrupted'] = str(error)
                        for row in self.report['scenarios']:
                            if row.get('reason') == 'not-executed':
                                row['reason'] = 'session-aborted:' + str(error)
                        break
                    if self.phase == 1:
                        self.request('show')
                if self.page:
                    self.page.close()
                    self.page = None
                self.retire()
        finally:
            # Never skip cleanup even after focus theft, UI loss, bad JSON/input.
            try:
                self.win.descendants(self.owned)
                atomic_json(self.directory / 'recipients.json', self.owned)
            except (Blocked, OSError) as error:
                self.report['cleanup']['errors'].append(str(error))
            cleanup = self.win.cleanup(self.owned)
            self.report['cleanup']['remaining'] = cleanup['remaining']
            self.report['cleanup']['errors'] += cleanup['errors']
            if self.page:
                self.page.close()
            # Delete only our registered, marked disposable app content after
            # verified absence. Journals/logs/state/screenshots remain evidence.
            if not cleanup['remaining'] and not self.report['cleanup']['errors']:
                for area in self.disposable:
                    try:
                        marker = bounded_json(area / '.harness-owner.json')
                        if area.is_symlink() or area.resolve().parent != self.directory or marker.get('run') != self.directory.name:
                            raise Blocked('disposable-ownership-lost')
                        shutil.rmtree(area)
                    except (Blocked, OSError, ValueError) as error:
                        self.report['cleanup']['errors'].append('disposable-cleanup:' + str(error))
            try:
                if self.phase == 1:
                    self.request('show')
                self.request('quit')
            except (Blocked, OSError, ValueError) as error:
                self.report['cleanup']['errors'].append('overlay-quit:' + str(error))
            # Restore only if last focus still belongs to this harness. Do not
            # fight a user who switched applications during the session.
            foreground = int(self.win.u.GetForegroundWindow() or 0)
            try:
                focused_pid = self.win.hwnd_pid(foreground) if foreground else 0
                if (foreground == 0 or any(r['pid'] == focused_pid for r in self.owned)) and self.original_record:
                    self.win.window(self.original_foreground, self.original_record)
                    self.win.u.SetForegroundWindow(self.original_foreground)
                    if self.cursor_saved:
                        self.win.u.SetCursorPos(self.original_cursor.x, self.original_cursor.y)
            except Blocked:
                pass
            self.report['durationMs'] = int((time.monotonic() - self.started) * 1000)
            self.save()
        return 0 if all(s['status'] == 'PASS' for s in self.report['scenarios']) and not self.report['cleanup']['remaining'] and not self.report['cleanup']['errors'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args(argv)
    try:
        win = Win32()
        if args.preflight:
            uia_ready, uia_reason = False, None
            try:
                uia = UIA()
                uia.close()
                uia_ready = True
            except (Blocked, OSError) as error:
                uia_reason = str(error)
            print(json.dumps({'windows': True, 'ctypes': True, 'uia': uia_ready, 'uiaReason': uia_reason, 'foregroundHwnd': str(win.u.GetForegroundWindow() or 0), 'inputSize': C.sizeof(win.Input)}))
            return 0
        root = Path(__file__).resolve().parents[2]
        if not args.root or args.root.resolve() != root or Path.cwd().resolve() != root or not args.directory:
            raise Blocked('active-worktree-only')
        directory = args.directory.resolve()
        if directory.parent != root or not directory.name.startswith('desktop-native-overlay-') or args.directory.is_symlink() or not directory.is_dir():
            raise Blocked('harness-owned-directory-required')
        return Acceptance(root, directory, win).run()
    except (Blocked, OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
