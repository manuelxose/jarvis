"""Packaging must never invoke the legacy machine provisioning entrypoint."""

from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

import jarvis


class PackagingTests(unittest.TestCase):
    def test_provisioning_is_separate_from_setuptools_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "setup.py").exists())
        with patch("subprocess.run", side_effect=AssertionError("provisioning on import")):
            module = runpy.run_path(str(root / "legacy_setup.py"))
        self.assertEqual(module["BASE_DIR"], root)
        self.assertTrue(callable(module["main"]))

    def test_package_exposes_version_without_removing_existing_exports(self):
        self.assertEqual(jarvis.__version__, "2.0.0")
        self.assertTrue(callable(jarvis.load_config))
        self.assertEqual(jarvis.RuntimeConfig.__module__, "jarvis.config")
