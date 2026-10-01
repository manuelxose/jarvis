"""`jarvis ui` command tests: daemon reply handling and launcher selection."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps.cli import main

TOKEN = "s3cret-token"
APP_URL = f"http://127.0.0.1:8765/#token={TOKEN}"
REPLY = {"ok": True, "url": "http://127.0.0.1:8765/events", "token": TOKEN, "app_url": APP_URL}


def run_ui(*flags: str, reply=REPLY, edge="C:/Edge/msedge.exe", send_error=None):
    out, err = io.StringIO(), io.StringIO()
    send = patch("jarvis.apps.daemon.send_command", side_effect=send_error) if send_error else patch("jarvis.apps.daemon.send_command", return_value=reply)
    with send, \
            patch("jarvis.apps.commands.shutil.which", return_value=edge), \
            patch("jarvis.apps.commands.os.environ", {}), \
            patch("jarvis.apps.commands.subprocess.Popen") as popen, \
            patch("jarvis.apps.commands.webbrowser.open") as browser:
        code = main(["ui", *flags], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue(), popen, browser


class UiCommandTests(unittest.TestCase):
    def test_daemon_down_returns_1_with_start_hint(self) -> None:
        for error in (ConnectionRefusedError("refused"), OSError("unreachable")):
            code, out, err, popen, browser = run_ui(send_error=error)
            self.assertEqual(code, 1)
            self.assertIn("start it with: jarvis daemon", err)
            popen.assert_not_called()
            browser.assert_not_called()

    def test_disabled_returns_1_with_daemon_error(self) -> None:
        code, out, err, popen, browser = run_ui(reply={"ok": False, "error": "ui events disabled; set daemon.ui_events_port"})
        self.assertEqual(code, 1)
        self.assertIn("ui events disabled", err)
        popen.assert_not_called()
        browser.assert_not_called()

    def test_older_daemon_without_app_url_returns_1(self) -> None:
        code, out, err, popen, browser = run_ui(reply={"ok": True, "url": "x", "token": TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("app_url", err)
        self.assertNotIn(TOKEN, err)
        browser.assert_not_called()

    def test_print_writes_app_url_and_launches_nothing(self) -> None:
        code, out, err, popen, browser = run_ui("--print")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), APP_URL)
        popen.assert_not_called()
        browser.assert_not_called()

    def test_edge_found_opens_app_window(self) -> None:
        code, out, err, popen, browser = run_ui()
        self.assertEqual(code, 0)
        self.assertEqual(popen.call_args.args[0], ["C:/Edge/msedge.exe", f"--app={APP_URL}"])
        browser.assert_not_called()
        self.assertIn("opened in Edge app window", out)

    def test_edge_missing_falls_back_to_default_browser(self) -> None:
        code, out, err, popen, browser = run_ui(edge=None)
        self.assertEqual(code, 0)
        popen.assert_not_called()
        browser.assert_called_once_with(APP_URL)
        self.assertIn("opened in default browser", out)

    def test_edge_launch_failure_falls_back_to_default_browser(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with patch("jarvis.apps.daemon.send_command", return_value=REPLY), \
                patch("jarvis.apps.commands.shutil.which", return_value="C:/Edge/msedge.exe"), \
                patch("jarvis.apps.commands.subprocess.Popen", side_effect=OSError("boom")), \
                patch("jarvis.apps.commands.webbrowser.open") as browser:
            code = main(["ui"], stdout=out, stderr=err)
        self.assertEqual(code, 0)
        browser.assert_called_once_with(APP_URL)
        self.assertNotIn(TOKEN, err.getvalue())

    def test_output_without_print_never_contains_token(self) -> None:
        for edge in ("C:/Edge/msedge.exe", None):
            code, out, err, popen, browser = run_ui(edge=edge)
            self.assertEqual(code, 0)
            self.assertNotIn(TOKEN, out)
            self.assertNotIn(TOKEN, err)


if __name__ == "__main__":
    unittest.main()
