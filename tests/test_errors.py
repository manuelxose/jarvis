import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.errors import (
    HermesError,
    JarvisError,
    ProviderConfigError,
    ProviderError,
    ProviderUnavailable,
    ToolError,
    ToolExecutionError,
    ToolPermissionDenied,
)


class ErrorFamilyTests(unittest.TestCase):
    def test_required_errors_all_subclass_jarvis_error(self):
        for cls in (
            ProviderUnavailable,
            ProviderConfigError,
            ToolPermissionDenied,
            ToolExecutionError,
            HermesError,
        ):
            self.assertTrue(issubclass(cls, JarvisError), f"{cls.__name__} must be a JarvisError")


class TransientMappingTests(unittest.TestCase):
    def test_transient_and_config_errors_are_distinct(self):
        transient = ProviderUnavailable("down", provider="p")
        config = ProviderConfigError("bad key", provider="p")

        self.assertTrue(transient.transient)
        self.assertFalse(config.transient)
        self.assertEqual(transient.provider, "p")
        self.assertEqual(config.provider, "p")

    def test_provider_error_defaults_to_transient(self):
        self.assertTrue(ProviderError("generic failure").transient)

    def test_unavailable_is_transient_config_is_not(self):
        self.assertTrue(ProviderUnavailable("down").transient)
        self.assertFalse(ProviderConfigError("missing key").transient)


class ErrorFamilyBoundaryTests(unittest.TestCase):
    def test_tool_errors_belong_to_tool_family_not_provider_family(self):
        self.assertTrue(issubclass(ToolPermissionDenied, ToolError))
        self.assertTrue(issubclass(ToolExecutionError, ToolError))
        self.assertFalse(issubclass(ToolPermissionDenied, ProviderError))
        self.assertFalse(issubclass(ToolExecutionError, ProviderError))

    def test_hermes_error_is_its_own_family(self):
        self.assertFalse(issubclass(HermesError, ProviderError))
        self.assertFalse(issubclass(HermesError, ToolError))
        self.assertTrue(issubclass(HermesError, JarvisError))

    def test_unavailable_is_provider_error(self):
        self.assertTrue(issubclass(ProviderUnavailable, ProviderError))
        self.assertTrue(issubclass(ProviderConfigError, ProviderError))


if __name__ == "__main__":
    unittest.main()
