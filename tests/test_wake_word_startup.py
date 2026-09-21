import ast
from pathlib import Path
import unittest


class WakeWordStartupTests(unittest.TestCase):
    def test_disabled_openwakeword_does_not_construct_a_wake_model(self):
        tree = ast.parse(Path("legacy/main.py").read_text(encoding="utf-8"))
        runtime_builder = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_runtime_components"
        )

        guarded_constructor = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "openwakeword_enabled"
            and any(
                isinstance(call.func, ast.Name) and call.func.id == "WakeWordListener"
                for statement in node.body
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            )
            for node in ast.walk(runtime_builder)
        )

        self.assertTrue(guarded_constructor)

    def test_run_receives_the_capture_backend_built_at_startup(self):
        tree = ast.parse(Path("legacy/main.py").read_text(encoding="utf-8"))
        runtime_builder = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_runtime_components"
        )
        run_function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"
        )

        returned_keys = {
            key.value
            for node in ast.walk(runtime_builder)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        assigned_backend = any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "capture_backend"
                for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            )
            and isinstance(node.value, ast.Subscript)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "components"
            for node in ast.walk(run_function)
        )

        self.assertIn("capture_backend", returned_keys)
        self.assertTrue(assigned_backend)
