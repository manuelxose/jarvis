import ast
from pathlib import Path
import unittest


class RuntimePerformanceTests(unittest.TestCase):
    def test_startup_does_not_pregenerate_before_ready(self):
        source = Path("main.py").read_text()
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        pregenerate = [n for n in calls if n.func.attr == "pregenerate_common_cache"]
        self.assertEqual([], pregenerate, "cache generation must not be on the readiness path")

    def test_inline_wake_command_does_not_require_second_capture(self):
        source = Path("main.py").read_text()
        self.assertIn("user_text = inline_command if inline_command else \"\"", source)
        self.assertNotIn("if inline_command:\n                    user_text = inline_command\n                else:\n                    user_text = stt_service.transcribe_from_mic()", source)
