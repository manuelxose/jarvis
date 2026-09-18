from pathlib import Path
import tempfile
import unittest

from voice.sapi_tts import select_voice, synthesize_to_file


class SapiTtsTests(unittest.TestCase):
    def test_select_voice_prefers_the_requested_language(self):
        voices = [
            type("Voice", (), {"id": "en", "name": "Microsoft David", "languages": []})(),
            type("Voice", (), {"id": "es", "name": "Microsoft Helena", "languages": []})(),
        ]

        self.assertEqual("es", select_voice(voices, "es"))

    def test_synthesize_to_file_configures_engine_and_writes_audio(self):
        class FakeEngine:
            def __init__(self):
                self.properties = {
                    "voices": [type("Voice", (), {"id": "es", "name": "Spanish", "languages": []})()],
                }
                self.saved_path = None
                self.set_values = {}

            def getProperty(self, name):
                return self.properties.get(name)

            def setProperty(self, name, value):
                self.set_values[name] = value

            def save_to_file(self, text, path):
                self.saved_path = path
                Path(path).write_bytes(b"RIFF-fake")

            def runAndWait(self):
                return None

            def stop(self):
                return None

        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as directory:
            output = synthesize_to_file(
                "Hola Jarvis",
                Path(directory) / "answer.wav",
                language="es",
                engine_factory=lambda: engine,
            )

            self.assertTrue(output.exists())
            self.assertEqual("es", engine.set_values["voice"])
            self.assertEqual(175, engine.set_values["rate"])
