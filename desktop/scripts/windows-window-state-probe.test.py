"""Platform-free contract tests of the window-state observer guards; never Windows acceptance."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).with_name('windows-window-state-probe.py')
spec = importlib.util.spec_from_file_location('window_state_probe', source)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Guards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ws-contract-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_run_path_escape(self):
        state = self.root / (probe.PREFIX + 'state-x')
        for bad in (self.root, self.root / 'other-dir', self.root.parent / (probe.PREFIX + 'run-x')):
            with self.assertRaisesRegex(probe.Blocked, 'run-path-escape'):
                probe.Probe(self.root, bad, state)

    def test_run_directory_symlink_blocked(self):
        real = self.root / 'real'; real.mkdir()
        link = self.root / (probe.PREFIX + 'run-link')
        link.symlink_to(real)
        with self.assertRaisesRegex(probe.Blocked, 'run-path-escape'):
            probe.Probe(self.root, link, self.root / (probe.PREFIX + 'state-x'))

    def test_state_path_escape(self):
        run = self.root / (probe.PREFIX + 'run-1'); run.mkdir()
        for bad in (self.root / 'state', self.root / 'sub' / (probe.PREFIX + 'state')):
            with self.assertRaisesRegex(probe.Blocked, 'state-path-escape'):
                probe.Probe(self.root, run, bad)

    def test_valid_paths_reach_windows_gate_on_linux(self):
        run = self.root / (probe.PREFIX + 'run-1') / 'x-1'; run.mkdir(parents=True)
        with patch.object(probe.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(probe.Blocked, 'windows-python-required'):
                probe.Probe(self.root, run, self.root / (probe.PREFIX + 'state-x'))

    def test_blocked_report_shape_and_exit(self):
        run = self.root / (probe.PREFIX + 'run-1'); run.mkdir()
        argv = ['--root', str(self.root), '--directory', str(run), '--state', str(self.root / (probe.PREFIX + 'state-x')),
                '--scenario', 'maximized-restore', '--launch', '1']
        with patch.object(probe.sys, 'platform', 'linux'):
            self.assertEqual(probe.main(argv), 1)
        report = json.loads((run / 'probe.json').read_text())
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertTrue(report['reason'])
        self.assertEqual(report['observations'], [])
        self.assertFalse(report['preflight']['ctypes'])

    def test_physical_display_change_is_never_driven(self):
        text = source.read_text()
        self.assertNotIn('ChangeDisplaySettings', text)
        run = self.root / (probe.PREFIX + 'run-1'); run.mkdir()
        argv = ['--root', str(self.root), '--directory', str(run), '--state', str(self.root / (probe.PREFIX + 'state-x')),
                '--scenario', 'physical-display-change', '--launch', '1']
        with patch.object(probe.sys, 'platform', 'linux'):
            self.assertEqual(probe.main(argv), 1)
        self.assertNotEqual(json.loads((run / 'probe.json').read_text())['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
