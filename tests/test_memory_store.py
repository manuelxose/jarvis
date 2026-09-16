import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.memory.store import MemoryStore
from jarvis.core.errors import MemoryError


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / "jarvis.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_persists_across_reopen(self):
        self.store.add("El usuario se llama Manuel", tier="long_term", category="nombre")
        self.store.close()

        reopened = MemoryStore(Path(self.temp.name) / "jarvis.db")
        records = reopened.list(tier="long_term")
        self.assertEqual(1, len(records))
        self.assertIn("Manuel", records[0].content)
        reopened.close()

    def test_recall_is_relevance_ranked_and_bounded(self):
        self.store.add("El usuario vive en Chipiona", tier="long_term", category="ubicacion")
        self.store.add("El usuario programa en Python", tier="long_term", category="skill")
        results = self.store.recall("donde vive el usuario", limit=2)
        self.assertTrue(results)
        self.assertLessEqual(len(results), 2)

    def test_does_not_dump_all_records_into_recall(self):
        for i in range(20):
            self.store.add(f"dato irrelevante numero {i}", tier="conversation")
        results = self.store.recall("donde vive el usuario", limit=3)
        self.assertLessEqual(len(results), 3)

    def test_refuses_secret_content(self):
        with self.assertRaises(MemoryError):
            self.store.add("mi api_key=sk-12345", tier="long_term")

    def test_delete_and_correct(self):
        record_id = self.store.add("temporal", tier="conversation")
        self.assertTrue(self.store.delete(record_id))
        self.assertIsNone(self.store.get(record_id))
        record_id = self.store.add("corregible", tier="conversation")
        self.assertTrue(self.store.correct(record_id, "corregido"))
        self.assertEqual("corregido", self.store.get(record_id).content)

    def test_promote_moves_to_long_term(self):
        record_id = self.store.add("importante", tier="conversation")
        self.assertTrue(self.store.promote(record_id))
        self.assertEqual("long_term", self.store.get(record_id).tier)

    def test_preferences(self):
        self.store.set_preference("idioma", "es")
        self.assertEqual({"idioma": "es"}, self.store.get_preferences())


if __name__ == "__main__":
    unittest.main()
