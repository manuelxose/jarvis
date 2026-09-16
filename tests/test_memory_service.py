import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.memory.service import MemoryService
from jarvis.adapters.memory.store import MemoryRecord, MemoryStore
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
        record_id = self.service.record_turn("user", "hola jarvis")
        self.assertIsInstance(record_id, int)
        inspected = self.service.inspect()
        self.assertTrue(any(r["tier"] == "conversation" for r in inspected))

    def test_record_turn_skips_blank(self):
        self.assertIsNone(self.service.record_turn("user", "   "))
        self.assertEqual([], self.service.inspect())

    # --- MemoryProvider contract wiring -------------------------------------

    def test_satisfies_memory_provider_contract(self):
        """recall(query, context) -> list[str] must match MemoryProvider."""
        self.assertTrue(inspect.iscoroutinefunction(MemoryService.recall))
        params = list(inspect.signature(MemoryService.recall).parameters)
        self.assertEqual(["self", "query", "context"], params)
        return_annotation = str(inspect.signature(MemoryService.recall).return_annotation)
        self.assertIn("list[str]", return_annotation)

    # --- Relevance-bounded retrieval / tier exclusion ----------------------

    async def test_recall_excludes_working_and_operational_tiers(self):
        self.service.remember("trabajo en curso activo", tier="working")
        self.service.remember("estado del sistema ok", tier="operational")
        self.service.remember("el usuario vive en Chipiona", tier="long_term")
        memories = await self.service.recall("activo estado Chipiona", TurnContext.fresh("c"))
        joined = " ".join(memories)
        self.assertIn("Chipiona", joined)
        self.assertNotIn("trabajo en curso activo", joined)
        self.assertNotIn("estado del sistema ok", joined)

    async def test_recall_is_bounded_by_max_recall(self):
        service = MemoryService(self.service.store, max_recall=2)
        for i in range(10):
            service.remember(f"recuerdo relevante numero {i}", tier="long_term")
        memories = await service.recall("recuerdo relevante", TurnContext.fresh("c"))
        self.assertLessEqual(len(memories), 2)

    # --- Promotion criteria -------------------------------------------------

    def test_promotion_criteria_defaults(self):
        self.assertEqual(
            {"min_touches": 2, "min_confidence": 0.95},
            self.service.promotion_criteria,
        )

    def test_meets_promotion_thresholds(self):
        ephemeral = MemoryRecord(1, "conversation", "turn", "x", "user", 0.3, 0.0, 0.0)
        self.assertFalse(self.service.meets_promotion(ephemeral, 0))
        self.assertFalse(self.service.meets_promotion(ephemeral, 1))
        self.assertTrue(self.service.meets_promotion(ephemeral, 2))

        explicit = MemoryRecord(2, "conversation", "general", "y", "user", 0.99, 0.0, 0.0)
        self.assertTrue(self.service.meets_promotion(explicit, 0))

    # --- Async consolidation ------------------------------------------------

    async def test_consolidate_promotes_recalled_conversation_record(self):
        record_id = self.service.record_turn("user", "me gusta la pizza margarita")
        await self.service.recall("pizza", TurnContext.fresh("c"))
        await self.service.recall("pizza margarita", TurnContext.fresh("c"))
        summary = await self.service.consolidate()
        self.assertGreaterEqual(summary["promoted"], 1)
        self.assertEqual("long_term", self.service.store.get(record_id).tier)

    async def test_consolidate_promotes_high_confidence_explicit_memory(self):
        record_id = self.service.remember(
            "el usuario vive en Chipiona", confidence=0.99
        )
        summary = await self.service.consolidate()
        self.assertEqual(1, summary["promoted"])
        self.assertEqual("long_term", self.service.store.get(record_id).tier)

    async def test_consolidate_does_not_promote_unused_turn(self):
        self.service.record_turn("user", "hola que tal")
        summary = await self.service.consolidate()
        self.assertEqual(0, summary["promoted"])
        self.assertEqual(1, summary["inspected"])

    # --- Redaction ----------------------------------------------------------

    async def test_redact_removes_record_and_search_index(self):
        record_id = self.service.remember("informacion sensible privada")
        self.assertTrue(await self.service.redact(record_id))
        self.assertIsNone(self.service.store.get(record_id))
        self.assertEqual([], await self.service.recall("sensible", TurnContext.fresh("c")))

    async def test_redact_missing_record_returns_false(self):
        self.assertFalse(await self.service.redact(999999))

    async def test_redact_content_scrubs_substring_and_reindexes(self):
        self.service.remember("el usuario vive en Chipiona", tier="long_term")
        changed = await self.service.redact_content("Chipiona")
        self.assertEqual(1, changed)
        self.assertIn("[redacted]", self.service.inspect()[0]["content"])
        self.assertNotIn("Chipiona", self.service.inspect()[0]["content"])
        self.assertEqual([], await self.service.recall("Chipiona", TurnContext.fresh("c")))

    async def test_redact_content_empty_token_is_noop(self):
        self.service.remember("dato cualquiera")
        self.assertEqual(0, await self.service.redact_content("   "))
        self.assertEqual(0, await self.service.redact_content(""))


if __name__ == "__main__":
    unittest.main()
