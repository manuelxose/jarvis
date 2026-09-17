import os
import sys
import tempfile
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
                "file",
                "clipboard",
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

    async def test_file_tool_reads_and_writes_within_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            try:
                os.chdir(tmp)
                tool = self.tools["file"]
                ack = await tool.execute(
                    {"action": "write", "path": "notas.txt", "content": "hola mundo"},
                    self.context(),
                )
                self.assertIn("notas.txt", ack)

                read = await tool.execute(
                    {"action": "read", "path": "notas.txt"}, self.context()
                )
                self.assertEqual("hola mundo", read)
            finally:
                os.chdir(original)

    async def test_file_tool_rejects_empty_path(self):
        result = await self.tools["file"].execute(
            {"action": "read", "path": "   "}, self.context()
        )
        self.assertEqual("Necesito una ruta de archivo.", result)

    async def test_file_tool_rejects_invalid_action(self):
        result = await self.tools["file"].execute(
            {"action": "copy", "path": "x.txt"}, self.context()
        )
        self.assertEqual("La accion de archivo debe ser 'read' o 'write'.", result)

    async def test_file_tool_rejects_path_outside_cwd(self):
        outside = str(Path.cwd().resolve().parent)
        result = await self.tools["file"].execute(
            {"action": "write", "path": outside, "content": "x"}, self.context()
        )
        self.assertEqual(
            "No puedo acceder a rutas fuera del directorio de trabajo.", result
        )

    async def test_file_tool_reports_missing_file_on_read(self):
        result = await self.tools["file"].execute(
            {"action": "read", "path": "no-existe.txt"}, self.context()
        )
        self.assertEqual("No he encontrado el archivo 'no-existe.txt'.", result)

    async def test_file_tool_truncates_long_reads(self):
        long_text = "x" * 1500
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            try:
                os.chdir(tmp)
                Path("grande.txt").write_text(long_text, encoding="utf-8")
                result = await self.tools["file"].execute(
                    {"action": "read", "path": "grande.txt"}, self.context()
                )
                self.assertTrue(result.endswith("..."))
                self.assertLess(len(result), len(long_text))
                self.assertEqual(1003, len(result))
            finally:
                os.chdir(original)


    async def test_clipboard_degrades_off_windows_without_subprocess(self):
        with patch("jarvis.adapters.tools.windows.subprocess.run") as run:
            read = await self.tools["clipboard"].execute({"action": "read"}, self.context())
            copy = await self.tools["clipboard"].execute(
                {"action": "copy", "content": "x"}, self.context()
            )

        self.assertEqual("El portapapeles no esta disponible fuera de Windows.", read)
        self.assertEqual("El portapapeles no esta disponible fuera de Windows.", copy)
        run.assert_not_called()

    async def test_clipboard_rejects_missing_or_invalid_action(self):
        result = await self.tools["clipboard"].execute({}, self.context())
        self.assertEqual("La accion de portapapeles debe ser 'read' o 'copy'.", result)

        result = await self.tools["clipboard"].execute({"action": "paste"}, self.context())
        self.assertEqual("La accion de portapapeles debe ser 'read' o 'copy'.", result)

    async def test_clipboard_read_invokes_powershell_on_windows(self):
        with patch("jarvis.adapters.tools.windows._IS_WINDOWS", True), patch(
            "jarvis.adapters.tools.windows.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "hello"
            result = await self.tools["clipboard"].execute({"action": "read"}, self.context())

        self.assertEqual("hello", result)
        run.assert_called_once()
        self.assertEqual(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            run.call_args.args[0],
        )

    async def test_clipboard_copy_uses_encoded_command_over_stdin(self):
        with patch("jarvis.adapters.tools.windows._IS_WINDOWS", True), patch(
            "jarvis.adapters.tools.windows.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            result = await self.tools["clipboard"].execute(
                {"action": "copy", "content": "hola mundo"}, self.context()
            )

        self.assertIn("portapapeles", result)
        args = run.call_args.args[0]
        self.assertEqual("powershell", args[0])
        self.assertIn("-EncodedCommand", args)
        self.assertEqual("hola mundo", run.call_args.kwargs["input"])

    async def test_clipboard_copy_requires_content(self):
        with patch("jarvis.adapters.tools.windows._IS_WINDOWS", True), patch(
            "jarvis.adapters.tools.windows.subprocess.run"
        ) as run:
            result = await self.tools["clipboard"].execute({"action": "copy"}, self.context())

        self.assertEqual("Necesito contenido para copiar al portapapeles.", result)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
