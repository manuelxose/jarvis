"""Autostart shortcut: Startup-folder target, quoting, UNC mapping and lifecycle, without touching Windows."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PureWindowsPath
from types import ModuleType, SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps import autostart, commands


class FakeShortcut:
    def __init__(self, path):
        self.path = path

    def Save(self):  # noqa: N802 - WScript.Shell API
        Path(self.path).write_text(f"{self.TargetPath}|{self.Arguments}", encoding="utf-8")


class FakeShell:
    created = []

    def CreateShortCut(self, path):  # noqa: N802
        shortcut = FakeShortcut(path)
        self.created.append(shortcut)
        return shortcut


def fake_win32com():
    client = ModuleType("win32com.client")
    client.Dispatch = lambda name: FakeShell() if name == "WScript.Shell" else (_ for _ in ()).throw(AssertionError(name))
    package = ModuleType("win32com")
    package.client = client
    return {"win32com": package, "win32com.client": client}


class AutostartTests(unittest.TestCase):
    def setUp(self):
        FakeShell.created = []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.startup = self.root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        self.startup.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"APPDATA": str(self.root)})
        self.env.start()
        self.modules = mock.patch.dict(sys.modules, fake_win32com())
        self.modules.start()
        self.interpreter = self.root / "py" / "python.exe"
        self.interpreter.parent.mkdir()
        self.interpreter.write_text("")
        (self.root / "py" / "pythonw.exe").write_text("")
        self.windows = mock.patch.object(autostart, "sys", SimpleNamespace(platform="win32", executable=str(self.interpreter)))
        self.windows.start()

    def tearDown(self):
        self.windows.stop()
        self.modules.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_install_targets_pythonw_launcher_and_quoted_config_in_the_temp_startup_folder(self):
        config = self.root / "my config" / "config.win.json"
        target = autostart.install(config)
        self.assertEqual(target, self.startup / autostart.SHORTCUT_NAME)
        self.assertTrue(target.is_relative_to(self.root))  # never the owner's real Startup folder
        shortcut = FakeShell.created[0]
        self.assertEqual(Path(shortcut.TargetPath), self.root / "py" / "pythonw.exe")
        self.assertEqual(shortcut.Arguments, f'"{autostart.LAUNCHER}" --config "{config}"')
        self.assertEqual(Path(shortcut.WorkingDirectory), autostart.PROJECT_ROOT)
        self.assertEqual(shortcut.WindowStyle, 7)
        self.assertTrue(autostart.LAUNCHER.name.endswith(".pyw"))

    def test_install_creates_a_missing_startup_folder(self):
        self.startup.rmdir()
        target = autostart.install(self.root / "config.json")
        self.assertTrue(target.exists())

    def test_pythonw_falls_back_to_the_interpreter_when_it_is_missing(self):
        (self.root / "py" / "pythonw.exe").unlink()
        self.assertEqual(autostart.pythonw(), self.interpreter)

    def test_install_status_remove_are_idempotent(self):
        self.assertFalse(autostart.status()["installed"])
        self.assertFalse(autostart.remove())  # nothing to remove is not an error
        autostart.install(self.root / "config.json")
        autostart.install(self.root / "config.json")  # overwrite, no duplicate entry
        self.assertEqual(list(self.startup.iterdir()), [self.startup / autostart.SHORTCUT_NAME])
        status = autostart.status()
        self.assertTrue(status["installed"])
        self.assertEqual(status["shortcut"], str(self.startup / autostart.SHORTCUT_NAME))
        self.assertEqual(status["pythonw"], str(self.root / "py" / "pythonw.exe"))
        self.assertTrue(autostart.remove())
        self.assertFalse(autostart.remove())
        self.assertFalse(autostart.status()["installed"])
        self.assertEqual(list(self.startup.iterdir()), [])

    def test_shortcut_uses_unc_paths_so_it_survives_the_shell_that_created_it(self):
        mapped = {autostart.LAUNCHER: "//srv/jarvis/launcher.pyw", autostart.PROJECT_ROOT: "//srv/jarvis"}
        with mock.patch.object(autostart, "unc", side_effect=lambda p: Path(mapped.get(Path(p), str(p)))):
            autostart.install(self.root / "config.json")
        shortcut = FakeShell.created[0]
        self.assertIn('"//srv/jarvis/launcher.pyw" --config', shortcut.Arguments)
        self.assertEqual(Path(shortcut.WorkingDirectory), Path("//srv/jarvis"))

    def test_unc_replaces_an_ephemeral_drive_letter_but_keeps_local_disks(self):
        class WindowsPath(PureWindowsPath):
            def resolve(self):
                return self

        connections = {"Z:": "\\\\server\\share"}

        def connection(drive):
            if drive not in connections:
                raise OSError("not a network drive")
            return connections[drive]

        win32wnet = ModuleType("win32wnet")
        win32wnet.WNetGetConnection = connection
        with mock.patch.object(autostart, "Path", WindowsPath), mock.patch.dict(sys.modules, {"win32wnet": win32wnet}):
            self.assertEqual(str(autostart.unc(WindowsPath("Z:\\jarvis\\scripts\\x.pyw"))), "\\\\server\\share\\jarvis\\scripts\\x.pyw")
            self.assertEqual(str(autostart.unc(WindowsPath("C:\\jarvis\\x.pyw"))), "C:\\jarvis\\x.pyw")  # local disk unchanged
        with mock.patch.object(autostart, "Path", WindowsPath), mock.patch.dict(sys.modules, {"win32wnet": None}):
            self.assertEqual(str(autostart.unc(WindowsPath("Z:\\jarvis"))), "Z:\\jarvis")  # pywin32 missing: keep the path

    def test_install_is_windows_only_and_writes_nothing_elsewhere(self):
        with mock.patch.object(autostart, "sys", SimpleNamespace(platform="linux", executable=str(self.interpreter))):
            with self.assertRaisesRegex(RuntimeError, "Windows-only"):
                autostart.install(self.root / "config.json")
        self.assertEqual(FakeShell.created, [])
        self.assertEqual(list(self.startup.iterdir()), [])

    def test_missing_appdata_is_reported_not_raised_by_status_and_cli(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("APPDATA")
            self.assertEqual(autostart.status()["installed"], False)
            self.assertIn("APPDATA", str(autostart.status()["detail"]))
            with self.assertRaisesRegex(RuntimeError, "APPDATA"):
                autostart.install(self.root / "config.json")
            with self.assertRaisesRegex(RuntimeError, "APPDATA"):
                autostart.remove()
            out, err = io.StringIO(), io.StringIO()
            args = SimpleNamespace(args=["install"], config=str(self.root / "config.json"))
            with redirect_stdout(out), redirect_stderr(err):
                code = commands._autostart(args, None, out, err)
            self.assertEqual(code, 1)
            self.assertIn("APPDATA", err.getvalue())

    def test_cli_rejects_unknown_actions(self):
        out, err = io.StringIO(), io.StringIO()
        code = commands._autostart(SimpleNamespace(args=["enable"], config="c.json"), None, out, err)
        self.assertEqual(code, 2)
        self.assertEqual(list(self.startup.iterdir()), [])

    def test_launcher_forwards_arguments_to_the_daemon_command(self):
        source = autostart.LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('main(["daemon", *sys.argv[1:]])', source)  # `--config <path>` from the shortcut reaches `jarvis daemon`
        self.assertIn("sys.stdout is None", source)  # pythonw has no console

    def test_shortcut_arguments_reach_the_daemon_with_the_chosen_config(self):
        import asyncio
        import json
        import shlex

        from jarvis.apps import cli, daemon

        config = self.root / "my config" / "config.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"runtime": {}, "memory": {"db_path": str(self.root / "j.db")}, "daemon": {"control_port": 47999, "hotkey": ""}}), encoding="utf-8")
        autostart.install(config)
        launcher, *argv = shlex.split(FakeShell.created[0].Arguments, posix=True)  # what Windows passes as sys.argv[1:]
        self.assertEqual(Path(launcher), autostart.LAUNCHER)
        seen = []

        async def fake_run_daemon(loaded):
            seen.append(loaded)
            return 0

        with mock.patch.object(daemon, "run_daemon", fake_run_daemon), \
                mock.patch.object(daemon, "configure_file_logging", return_value=self.root / "daemon.log"):
            errors = io.StringIO()
            code = cli.main(["daemon", *argv], stdout=io.StringIO(), stderr=errors)
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(seen[0].daemon["control_port"], 47999)
        asyncio.set_event_loop(None)


if __name__ == "__main__":
    unittest.main()
