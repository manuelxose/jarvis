import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.application.routing import FastCommandClassifier, Router, normalize
from jarvis.core.turn import TurnContext


class _FakeIntentClassifier:
    """Test double for the optional IntentClassifier port."""

    def __init__(self, result: str | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def classify(self, text: str, context: TurnContext) -> str:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FastCommandClassifierTests(unittest.TestCase):
    def setUp(self):
        self.classifier = FastCommandClassifier()

    def test_volume_up_matches_deterministically(self):
        match = self.classifier.match("sube el volumen")
        self.assertIsNotNone(match)
        self.assertEqual("volume_up", match.name)
        self.assertGreaterEqual(match.confidence, 0.9)

    def test_open_application_extracts_argument(self):
        match = self.classifier.match("abre spotify")
        self.assertIsNotNone(match)
        self.assertEqual("open_application", match.name)
        self.assertIn("spotify", match.arguments.get("application", ""))

    def test_time_and_date(self):
        self.assertEqual("time", self.classifier.match("que hora es").name)
        self.assertEqual("date", self.classifier.match("que dia es hoy").name)

    def test_non_command_returns_none(self):
        self.assertIsNone(self.classifier.match("cual es la capital de francia"))

    def test_empty_and_whitespace_return_none(self):
        self.assertIsNone(self.classifier.match(""))
        self.assertIsNone(self.classifier.match("   "))

    def test_punctuation_only_returns_none(self):
        self.assertIsNone(self.classifier.match("... !!!"))

    def test_volume_set_extracts_numeric_level(self):
        match = self.classifier.match("pon el volumen al 50")
        self.assertIsNotNone(match)
        self.assertEqual("volume_set", match.name)
        self.assertEqual("50", match.arguments.get("level"))

    def test_open_url_extracts_url(self):
        match = self.classifier.match("abre https://example.com")
        self.assertIsNotNone(match)
        self.assertEqual("open_url", match.name)
        self.assertEqual("https://example.com", match.arguments.get("url"))

    def test_normalize_folds_accents(self):
        self.assertEqual("sube el volumen", normalize("¡Sube el volumen!"))

    def test_normalize_empty_and_none(self):
        self.assertEqual("", normalize(""))
        self.assertEqual("", normalize(None))


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_to_fast_command(self):
        router = Router()
        decision = await router.route("sube el volumen", TurnContext.fresh("c"))
        self.assertEqual("fast_command", decision.route)
        self.assertEqual("volume_up", decision.command.name)

    async def test_routes_conversation_to_fast_model(self):
        router = Router()
        decision = await router.route("cual es la capital de francia", TurnContext.fresh("c"))
        self.assertEqual("fast_model", decision.route)

    async def test_routes_agentic_cue_to_hermes(self):
        router = Router()
        decision = await router.route("planifica una tarea", TurnContext.fresh("c"))
        self.assertEqual("hermes", decision.route)

    async def test_decision_is_inspectable(self):
        router = Router()
        decision = await router.route("sube el volumen", TurnContext.fresh("c"))
        as_dict = decision.as_dict()
        self.assertEqual("fast_command", as_dict["route"])
        self.assertIn("command", as_dict)

    async def test_fast_model_decision_omits_command(self):
        router = Router()
        decision = await router.route("cual es la capital de francia", TurnContext.fresh("c"))
        self.assertIsNone(decision.command)
        self.assertNotIn("command", decision.as_dict())

    async def test_empty_text_routes_to_fast_model(self):
        router = Router()
        decision = await router.route("   ", TurnContext.fresh("c"))
        self.assertEqual("fast_model", decision.route)

    async def test_intent_classifier_routes_agent_to_hermes(self):
        router = Router(intent_classifier=_FakeIntentClassifier("hermes"))
        decision = await router.route("cual es la capital de francia", TurnContext.fresh("c"))
        self.assertEqual("hermes", decision.route)

    async def test_intent_classifier_agent_alias_routes_to_hermes(self):
        router = Router(intent_classifier=_FakeIntentClassifier("agent"))
        decision = await router.route("cual es la capital de francia", TurnContext.fresh("c"))
        self.assertEqual("hermes", decision.route)

    async def test_intent_classifier_non_agent_falls_through_to_fast_model(self):
        router = Router(intent_classifier=_FakeIntentClassifier("fast_model"))
        decision = await router.route("cual es la capital de francia", TurnContext.fresh("c"))
        self.assertEqual("fast_model", decision.route)

    async def test_intent_classifier_failure_falls_back_to_fast_model(self):
        router = Router(intent_classifier=_FakeIntentClassifier(error=RuntimeError("boom")))
        with self.assertLogs("jarvis.application.routing", level="WARNING"):
            decision = await router.route("cual es la capital de francia", TurnContext.fresh("c"))
        self.assertEqual("fast_model", decision.route)


if __name__ == "__main__":
    unittest.main()
