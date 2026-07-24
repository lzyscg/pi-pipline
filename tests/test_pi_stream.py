from __future__ import annotations

import json
import os
import stat
import tempfile
import asyncio
import unittest
from pathlib import Path

from app.config import RoleProfile
from app.pi_stream import MAX_PI_EVENT_BYTES, PiEventParser, PiStreamRunner


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


class PiProcessGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_kills_the_whole_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "child.pid"
            fake_pi = root / "fake-pi"
            fake_pi.write_text(
                "#!/usr/bin/env python3\n"
                "import subprocess,time\n"
                f"p=subprocess.Popen(['sleep','30']);open({str(pid_file)!r},'w').write(str(p.pid))\n"
                "print('{\"type\":\"session\",\"version\":3,\"id\":\"fake\"}',flush=True)\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            fake_pi.chmod(fake_pi.stat().st_mode | stat.S_IXUSR)
            runner = PiStreamRunner(str(fake_pi))
            events = []

            async def emit(kind, payload):
                events.append((kind, payload))

            task = asyncio.create_task(
                runner.run(
                    role="generator",
                    role_profile=RoleProfile("x/y", "off", "lite-song-generator", True),
                    session_dir=root / "sessions",
                    session_id="fake",
                    system_prompt="x",
                    task_prompt="x",
                    token="stop-token",
                    emit=emit,
                    timeout_seconds=20,
                )
            )
            for _ in range(100):
                if pid_file.exists():
                    break
                await asyncio.sleep(0.02)
            child_pid = int(pid_file.read_text())
            self.assertTrue(await runner.stop("stop-token"))
            result = await task
            self.assertEqual(result.attempt_status, "killed")
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
