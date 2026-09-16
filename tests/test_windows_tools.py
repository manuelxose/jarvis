import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.windows import build_windows_tools
from jarvis.core.turn import TurnContext


class WindowsToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tools = {tool.name: tool for tool in build_windows_tools()}

    def context(self) -> TurnContext:
        return TurnContext.fresh("windows-tools-test")

    async def test_builds_exact_fixed_command_tool_set(self):
        self.assertEqual(
            {
                "time",
                "date",
                "system_info",
                "open_application",
                "open_url",
                "volume_up",
                "volume_down",
                "volume_set",
                "mute",
                "unmute",
                "media_play_pause",
                "media_next",
                "media_previous",
                "stop",
                "repeat",
            },
            set(self.tools),
        )

    async def test_time_date_and_system_info_are_available(self):
        self.assertTrue((await self.tools["time"].execute({}, self.context())).startswith("Son las "))
        self.assertTrue((await self.tools["time"].execute({}, self.context())).endswith("."))
        self.assertTrue((await self.tools["date"].execute({}, self.context())).startswith("Hoy es "))

        system_info = await self.tools["system_info"].execute({}, self.context())
        self.assertTrue(system_info.startswith("Sistema: "))
        self.assertIn(" nucleos", system_info)

    async def test_open_application_handles_known_and_unknown_apps_without_subprocesses(self):
        with patch("jarvis.adapters.tools.windows.subprocess.Popen") as popen:
            known = await self.tools["open_application"].execute(
                {"application": "notepad"}, self.context()
            )
            unknown = await self.tools["open_application"].execute(
                {"application": "not-a-real-app"}, self.context()
            )

        self.assertIn("no esta disponible fuera de Windows", known)
        self.assertIn("No se como abrir", unknown)
        popen.assert_not_called()

    async def test_open_url_only_allows_http_and_https(self):
        with patch("jarvis.adapters.tools.windows.webbrowser.open", return_value=True) as open_browser:
            unsupported = await self.tools["open_url"].execute(
                {"url": "ftp://example.com"}, self.context()
            )
            self.assertEqual("Solo abro enlaces http o https.", unsupported)
            open_browser.assert_not_called()

            opened = await self.tools["open_url"].execute(
                {"url": "https://example.com"}, self.context()
            )

        self.assertEqual("Abriendo el enlace.", opened)
        open_browser.assert_called_once_with("https://example.com")

    async def test_windows_gated_controls_degrade_without_subprocesses(self):
        gated_names = {
            "volume_up",
            "volume_down",
            "mute",
            "unmute",
            "media_play_pause",
            "media_next",
            "media_previous",
        }
        with patch("jarvis.adapters.tools.windows.subprocess.run") as run:
            for name in gated_names:
                with self.subTest(tool=name):
                    result = await self.tools[name].execute({}, self.context())
                    self.assertIn("no esta disponible fuera de Windows", result)

        run.assert_not_called()

    async def test_volume_set_degrades_without_subprocesses(self):
        with patch("jarvis.adapters.tools.windows.subprocess.run") as run:
            result = await self.tools["volume_set"].execute({"level": 50}, self.context())

        self.assertEqual("El control de volumen no esta disponible fuera de Windows.", result)
        run.assert_not_called()

    async def test_stop_cancels_context_and_repeat_acknowledges(self):
        context = self.context()

        self.assertEqual("Hecho.", await self.tools["stop"].execute({}, context))
        self.assertTrue(context.cancellation.cancelled)
        self.assertEqual("Hecho.", await self.tools["repeat"].execute({}, self.context()))


if __name__ == "__main__":
    unittest.main()
