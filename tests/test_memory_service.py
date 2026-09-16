import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.memory.service import MemoryService
from jarvis.adapters.memory.store import MemoryStore
from jarvis.core.turn import TurnContext


class MemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = MemoryService(MemoryStore(Path(self.temp.name) / "jarvis.db"))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    async def test_recall_returns_relevant_memories(self):
        self.service.remember("El usuario se llama Manuel", tier="long_term", category="nombre")
        memories = await self.service.recall("como se llama el usuario", TurnContext.fresh("c"))
        self.assertIn("El usuario se llama Manuel", memories)

    async def test_recall_returns_empty_without_matches(self):
        memories = await self.service.recall("zzz nada relacionado", TurnContext.fresh("c"))
        self.assertEqual([], memories)

    def test_record_turn_and_inspect(self):
        self.service.record_turn("user", "hola jarvis")
        inspected = self.service.inspect()
        self.assertTrue(any(r["tier"] == "conversation" for r in inspected))


if __name__ == "__main__":
    unittest.main()
