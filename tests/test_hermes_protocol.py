import io
import json
import unittest

from jarvis.adapters.hermes import protocol


class HermesMessageTests(unittest.TestCase):
    def test_encoded_message_round_trips_with_required_fields(self):
        encoded = protocol.encode_message(
            "request-1", "turn-1", protocol.PARTIAL_RESPONSE, {"text": "hola"}
        )

        message = protocol.parse_message(encoded)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual("request-1", message.request_id)
        self.assertEqual("turn-1", message.turn_id)
        self.assertEqual(protocol.PARTIAL_RESPONSE, message.event_type)
        self.assertEqual({"text": "hola"}, message.payload)
        self.assertIsInstance(message.timestamp, float)

    def test_stream_parser_yields_only_valid_json_lines(self):
        valid = json.dumps(
            {
                "request_id": "request-1",
                "turn_id": "turn-1",
                "event_type": protocol.STARTED,
                "payload": {},
                "timestamp": 123.0,
            }
        )
        lines = io.StringIO(f"\nnot json\n{valid}\n[]\n")

        messages = list(protocol.parse_messages(lines))

        self.assertEqual(1, len(messages))
        self.assertEqual(protocol.STARTED, messages[0].event_type)

    def test_rejects_missing_or_invalid_required_fields(self):
        valid = {
            "request_id": "request-1",
            "turn_id": "turn-1",
            "event_type": protocol.COMPLETED,
            "payload": {},
            "timestamp": 123.0,
        }
        invalid_messages = (
            {},
            {**valid, "request_id": ""},
            {**valid, "payload": []},
            {**valid, "timestamp": True},
            {**valid, "timestamp": float("inf")},
        )

        for invalid in invalid_messages:
            with self.subTest(invalid=invalid):
                self.assertIsNone(protocol.parse_message(json.dumps(invalid)))


if __name__ == "__main__":
    unittest.main()
