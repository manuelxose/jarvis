import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.application.routing import FastCommandClassifier, Router, normalize
from jarvis.core.turn import TurnContext


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

    def test_normalize_folds_accents(self):
        self.assertEqual("sube el volumen", normalize("¡Sube el volumen!"))


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


if __name__ == "__main__":
    unittest.main()
