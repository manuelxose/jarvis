import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.errors import (
    JarvisError,
    ProviderConfigError,
    ProviderUnavailable,
    ToolPermissionDenied,
)


class ErrorTests(unittest.TestCase):
    def test_transient_and_config_errors_are_distinct(self):
        transient = ProviderUnavailable("down", provider="p")
        config = ProviderConfigError("bad key", provider="p")

        self.assertTrue(transient.transient)
        self.assertFalse(config.transient)
        self.assertEqual(transient.provider, "p")
        self.assertTrue(issubclass(ProviderUnavailable, JarvisError))
        self.assertTrue(issubclass(ProviderConfigError, JarvisError))
        self.assertTrue(issubclass(ToolPermissionDenied, JarvisError))


if __name__ == "__main__":
    unittest.main()
