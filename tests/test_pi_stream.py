from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.pi_stream import MAX_PI_EVENT_BYTES, PiEventParser


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pi_events" / "probe_v0.81.1.jsonl"


class PiEventParserTests(unittest.TestCase):
    def test_real_probe_fixture_separates_thinking_text_and_terminal(self):
        parser = PiEventParser()
        kinds: list[str] = []
        thinking = ""
        text = ""
        for line in FIXTURE.read_bytes().splitlines():
            for event in parser.feed_line(line):
                kinds.append(event.kind)
                if event.kind == "thinking_delta":
                    thinking += event.payload["text"]
                if event.kind == "text_delta":
                    text += event.payload["text"]
        self.assertIn("[redacted probe thinking]", thinking)
        self.assertEqual(text, "PROBE_OK")
        self.assertEqual(parser.final_text, "PROBE_OK")
        self.assertTrue(parser.terminal_seen)
        self.assertIn("pi_terminal", kinds)

    def test_unknown_and_large_events_never_complete_business_output(self):
        parser = PiEventParser()
        unknown = parser.feed_line(json.dumps({"type": "future_event", "secret": "x"}))
        large = parser.feed_line(b"x" * (MAX_PI_EVENT_BYTES + 1))
        self.assertEqual(unknown[0].kind, "unknown_pi_event")
        self.assertNotIn("secret", unknown[0].payload)
        self.assertEqual(large[0].payload["reason"], "event_too_large")
        self.assertFalse(parser.terminal_seen)
        self.assertEqual(parser.final_text, "")

    def test_thinking_never_becomes_final_text(self):
        parser = PiEventParser()
        parser.feed_line(
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "delta": "ACTION: DELIVER",
                    },
                }
            )
        )
        self.assertEqual(parser.final_text, "")


if __name__ == "__main__":
    unittest.main()

