"""Platform-free contract tests of production guards; never Windows acceptance."""
import ctypes as C
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

source = Path(__file__).with_name('windows-overlay-probe.py')
spec = importlib.util.spec_from_file_location('overlay_probe', source)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
RECORD = {'pid': 123, 'path': 'C:\\owned.exe', 'started': '638000000000000001'}


class Guards(unittest.TestCase):
    def test_import_no_native_launch(self):
        self.assertEqual(len(probe.REQUIRED), 25)
        with patch.object(probe.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(probe.Blocked, 'windows-python-required'):
                probe.Win32()

    def test_null_uia_interface_blocks_before_vtable_access(self):
        uia = probe.UIA.__new__(probe.UIA)
        for obj in (None, C.c_void_p()):
            with self.assertRaisesRegex(probe.Blocked, 'uia-null-interface'):
                uia.call(obj, 10)

    def test_null_uia_bounds_block_before_array_access(self):
        class Value(C.Union):
            _fields_ = [('pointer', C.c_void_p)]
        class Variant(C.Structure):
            _fields_ = [('vt', C.c_ushort), ('value', Value)]
        uia = probe.UIA.__new__(probe.UIA)
        uia.Variant = Variant
        calls = []
        def property_call(obj, index, *args):
            args[-1][1]._obj.vt = 8197
        uia.call = property_call
        uia.auto = SimpleNamespace(VariantClear=lambda value: calls.append('cleared'))
        with self.assertRaisesRegex(probe.Blocked, 'uia-invalid-bounds'):
            uia.property(1, 30001)
        self.assertEqual(calls, ['cleared'])

    def test_bounded_poll(self):
        start = time.monotonic()
        with self.assertRaisesRegex(probe.Blocked, 'observation-timeout'):
            probe.poll(lambda: False, .05)
        self.assertLess(time.monotonic() - start, .3)

    def test_json_budget_malformed_symlink(self):
        with tempfile.TemporaryDirectory(prefix='overlay-contract-', dir=Path.cwd()) as directory:
            root = Path(directory)
            file = root / 'value.json'
            probe.atomic_json(file, {'ok': True})
            self.assertEqual(probe.bounded_json(file), {'ok': True})
            file.write_text('{')
            with self.assertRaises(ValueError):
                probe.bounded_json(file)
            file.write_bytes(b' ' * (4 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(probe.Blocked, 'budget'):
                probe.bounded_json(file)
            link = root / 'link.json'
            link.symlink_to(file)
            with self.assertRaisesRegex(probe.Blocked, 'symlink'):
                probe.bounded_json(link)

    def test_atomic_replace_retries_transient_sharing_denial(self):
        with tempfile.TemporaryDirectory(prefix='overlay-contract-', dir=Path.cwd()) as directory:
            file = Path(directory) / 'journal.json'
            probe.atomic_json(file, {'old': True})
            replace = probe.os.replace
            calls = []
            def transient(source, target):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError('sharing violation')
                replace(source, target)
            with patch.object(probe.os, 'replace', side_effect=transient), patch.object(probe.time, 'sleep') as sleep:
                probe.atomic_json(file, {'new': True})
            self.assertEqual(len(calls), 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(probe.bounded_json(file), {'new': True})

    def test_atomic_replace_permanent_denial_keeps_old_journal(self):
        with tempfile.TemporaryDirectory(prefix='overlay-contract-', dir=Path.cwd()) as directory:
            file = Path(directory) / 'journal.json'
            probe.atomic_json(file, {'old': True})
            with patch.object(probe.os, 'replace', side_effect=PermissionError('denied')) as replace, patch.object(probe.time, 'sleep') as sleep:
                with self.assertRaises(PermissionError):
                    probe.atomic_json(file, {'new': True})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(sleep.call_count, 5)
            self.assertEqual(probe.bounded_json(file), {'old': True})

    def test_window_reuse_hidden_and_identity_loss(self):
        win = probe.Win32.__new__(probe.Win32)
        win.u = SimpleNamespace(IsWindow=lambda _: True, IsWindowVisible=lambda _: True)
        win.hwnd_pid = lambda _: 123
        win.matches = lambda _: True
        win.window(100, RECORD)
        win.hwnd_pid = lambda _: 124
        with self.assertRaisesRegex(probe.Blocked, 'identity-changed'):
            win.window(100, RECORD)
        win.hwnd_pid = lambda _: 123
        win.matches = lambda _: False
        with self.assertRaisesRegex(probe.Blocked, 'identity-changed'):
            win.window(100, RECORD)
        win.matches = lambda _: True
        win.u.IsWindowVisible = lambda _: False
        with self.assertRaisesRegex(probe.Blocked, 'not-visible'):
            win.window(100, RECORD)
        win.window(100, RECORD, visible=False)

    def input_win(self):
        win = probe.Win32.__new__(probe.Win32)
        win.events = []
        win.u = SimpleNamespace(GetForegroundWindow=lambda: 100, SetCursorPos=lambda x, y: win.events.append(('move', x, y)) or True, SendInput=lambda *args: win.events.append('send') or 1)
        win.window = lambda *args: None
        win.rect = lambda *args: {'x': 0, 'y': 0, 'width': 800, 'height': 600}
        win.point = lambda hwnd, x, y: {'x': x + 10, 'y': y + 20}
        win.process = lambda pid: RECORD.copy()
        win.hit = lambda _: 100
        class Packet(C.Structure):
            _fields_ = [('type', C.c_int)]
            def __init__(self):
                super().__init__()
                self.data = SimpleNamespace(mi=None)
        win.Input, win.Mouse = Packet, lambda *args: args
        return win

    def test_off_target_and_malformed_points_do_not_move_or_send(self):
        for point in ({'x': -1, 'y': 1}, {'x': 800, 'y': 1}, {'x': 1, 'y': 600}, {'x': 1.5, 'y': 1}, {'x': True, 'y': 1}, {'x': float('nan'), 'y': 1}):
            win = self.input_win()
            with self.assertRaisesRegex(probe.Blocked, 'off-target'):
                win.input(100, RECORD, point, {100}, (2,))
            self.assertEqual(win.events, [])

    def test_focus_theft_blocks_before_movement(self):
        win = self.input_win()
        win.u.GetForegroundWindow = lambda: 999
        with self.assertRaisesRegex(probe.Blocked, 'focus-stolen'):
            win.input(100, RECORD, {'x': 1, 'y': 1}, {100}, (2,))
        self.assertEqual(win.events, [])

    def test_foreign_hit_blocks_before_movement(self):
        win = self.input_win()
        win.hit = lambda _: 999
        with self.assertRaisesRegex(probe.Blocked, 'foreign-window'):
            win.input(100, RECORD, {'x': 1, 'y': 1}, {100}, (2,))
        self.assertEqual(win.events, [])

    def test_revalidate_after_movement_and_before_send(self):
        win = self.input_win()
        win.u.SetCursorPos = lambda x, y: setattr(win.u, 'GetForegroundWindow', lambda: 999) or True
        with patch.object(probe.time, 'sleep'):
            with self.assertRaisesRegex(probe.Blocked, 'changed-before-input'):
                win.input(100, RECORD, {'x': 1, 'y': 1}, {100}, (2,))
        self.assertNotIn('send', win.events)

    def test_input_observes_exact_native_identity_and_screen_conversion(self):
        win = self.input_win()
        with patch.object(probe.time, 'sleep'):
            result = win.input(100, RECORD, {'x': 1, 'y': 2}, {100}, (2,))
        self.assertEqual(result['screen'], {'x': 11, 'y': 22})
        self.assertEqual(result['revalidated'], RECORD)
        self.assertEqual(win.events, [('move', 11, 22), 'send'])

    def test_click_failure_releases_held_mouse_in_finally(self):
        acceptance = probe.Acceptance.__new__(probe.Acceptance)
        acceptance.budget = lambda: None
        acceptance.inputs = 0
        events = []
        def inject(hwnd, record, point, allowed, flags, **kwargs):
            events.append(flags)
            if flags == (4,):
                raise probe.Blocked('target-lost')
            return {'evidence': True}
        acceptance.win = SimpleNamespace(input=inject, release=lambda: events.append('release'))
        with self.assertRaisesRegex(probe.Blocked, 'target-lost'):
            acceptance.click(100, RECORD, {'x': 1, 'y': 1}, {100})
        self.assertEqual(events, [(2,), (4,), 'release'])

    def test_hidden_missing_nonfinite_control_bounds(self):
        acceptance = probe.Acceptance.__new__(probe.Acceptance)
        for value in (None, {}, {'x': 0, 'y': 0, 'width': 0, 'height': 1}):
            with self.assertRaises(probe.Blocked):
                acceptance.center({'geometry': {'toggle': value}}, 'toggle')

    def test_session_load_budget(self):
        acceptance = probe.Acceptance.__new__(probe.Acceptance)
        acceptance.started, acceptance.inputs, acceptance.shots = time.monotonic(), 0, 0
        acceptance.budget()
        for field, value in (('inputs', 32), ('shots', 32), ('started', time.monotonic() - 86)):
            previous = getattr(acceptance, field)
            setattr(acceptance, field, value)
            with self.assertRaisesRegex(probe.Blocked, 'session-budget'):
                acceptance.budget()
            setattr(acceptance, field, previous)

    def test_cleanup_revalidates_held_handle_skips_pid_reuse(self):
        win = probe.Win32.__new__(probe.Win32)
        calls = []
        win.k = SimpleNamespace(OpenProcess=lambda *args: 456, CloseHandle=lambda handle: calls.append(('close', handle)), TerminateProcess=lambda *args: calls.append(('terminate', args)) or True)
        win.process = lambda pid, handle: {**RECORD, 'started': '638000000000000099'}
        win.matches = lambda record: False
        self.assertEqual(win.cleanup([RECORD]), {'remaining': [], 'errors': []})
        self.assertEqual(calls, [('close', 456)])

    def test_cleanup_access_denied_cannot_be_inferred_as_absence(self):
        win = probe.Win32.__new__(probe.Win32)
        win.k = SimpleNamespace(OpenProcess=lambda *args: None)
        win.matches = lambda record: False
        with patch.object(probe.C, 'get_last_error', return_value=5, create=True):
            self.assertTrue(win.cleanup([RECORD])['errors'])
        with patch.object(probe.C, 'get_last_error', return_value=87, create=True):
            self.assertEqual(win.cleanup([RECORD]), {'remaining': [], 'errors': []})

    def test_screenshot_budget_before_native_allocations(self):
        win = probe.Win32.__new__(probe.Win32)
        with self.assertRaisesRegex(probe.Blocked, 'screenshot-budget'):
            win.screenshot(Path('not-written.bmp'), {'width': 1601, 'height': 1000})


class ReceiptServer(unittest.TestCase):
    def setUp(self):
        self.page = probe.Page()
        self.client = http.client.HTTPConnection('127.0.0.1', self.page.server.server_port, timeout=2)

    def tearDown(self):
        self.client.close()
        self.page.close()
        self.assertFalse(self.page.thread.is_alive())

    def send(self, value, path=None, length=None):
        body = json.dumps(value)
        self.client.request('POST', path or '/' + self.page.token, body, {'Content-Length': str(len(body) if length is None else length)})
        response = self.client.getresponse()
        response.read()
        return response.status

    def value(self, kind='pointer'):
        return {'kind': kind, 'x': 20, 'y': 30, 'pointerScreenX': 100, 'pointerScreenY': 200, 'width': 800, 'height': 600, 'screenX': 0, 'screenY': 0, 'outerWidth': 800, 'outerHeight': 640, 'dpr': 1}

    def test_independent_receipt_ack_and_safe_static_page(self):
        self.client.request('GET', '/' + self.page.token)
        response = self.client.getresponse()
        text = response.read().decode()
        self.assertIn('e.isTrusted', text)
        self.assertNotIn('eval(', text)
        self.assertEqual(self.send(self.value('ready')), 204)
        self.assertEqual(len(self.page.receipts), 0)
        self.assertEqual(self.send(self.value()), 204)
        self.assertEqual(self.page.receipts[0]['pointerScreenX'], 100)

    def test_bad_json_token_coordinates_and_receipt_count(self):
        for value in ({}, {**self.value(), 'x': '20'}, {**self.value(), 'kind': 'renderer-boolean'}, {**self.value(), 'dpr': 1.5}):
            self.assertEqual(self.send(value), 400)
        self.assertEqual(self.send(self.value(), '/foreign'), 400)
        self.assertEqual(self.send(self.value(), length=2049), 400)
        self.page.receipts = [self.value()] * 32
        self.assertEqual(self.send(self.value()), 400)


if __name__ == '__main__':
    unittest.main()
