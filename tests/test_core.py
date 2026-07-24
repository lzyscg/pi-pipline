from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.canonical import (
    InputContractError,
    canonicalize_lyric,
    changed_line_numbers,
    normalize_golden_line,
)
from app.contracts import (
    ContractError,
    parse_generation_result,
    parse_review_result,
    parse_supervisor_result,
)
from app.journal import CaseJournal
from app.redaction import redact_text
from app.state import (
    TurnState,
    TurnStatus,
    allowed_actions,
)
from app.validation_adapter import validate_canonical, validate_local_repair


GOLDEN = "我的那个心上人"
VALID_LYRIC = """山风吹过青石坡
阿妹河边洗衣裳
木槌声声催日落
阿哥隔岸唱山歌

歌声越过清清水
妹把衣裳放石旁
抬头应他山中调
两岸回声绕山梁

我的那个心上人
你在对岸等月光
我把心声唱给你
木叶轻轻落水上

我的那个心上人
今夜歌声作桥梁
明朝同走青石路
山花开满旧村庄"""


class CanonicalTests(unittest.TestCase):
    def test_canonical_is_exactly_four_by_four(self):
        lyric = canonicalize_lyric(VALID_LYRIC)
        self.assertEqual(len(lyric.lines), 16)
        self.assertEqual([len(stanza) for stanza in lyric.stanzas], [4, 4, 4, 4])
        self.assertEqual(lyric.lines[8], GOLDEN)
        self.assertEqual(lyric.lines[12], GOLDEN)

    def test_rejects_wrong_blank_boundary_and_controls(self):
        with self.assertRaises(InputContractError):
            canonicalize_lyric(VALID_LYRIC.replace("\n\n", "\n\n\n", 1))
        with self.assertRaises(InputContractError):
            normalize_golden_line("金句\n第二行")
        with self.assertRaises(InputContractError):
            normalize_golden_line("金句\x00")

    def test_diff_uses_canonical_line_numbers(self):
        before = canonicalize_lyric(VALID_LYRIC)
        after = canonicalize_lyric(VALID_LYRIC.replace("阿哥隔岸唱山歌", "阿哥隔岸把歌唱"))
        self.assertEqual(changed_line_numbers(before, after), [4])


class ContractTests(unittest.TestCase):
    def test_three_valid_contracts(self):
        supervisor = parse_supervisor_result(
            "# SupervisorResult v1\nACTION: SEND_GENERATOR\nMESSAGE:\n请创作完整歌词"
        )
        generation = parse_generation_result(
            f"# GenerationResult v1\nSUMMARY:\n完成初稿\nLYRIC:\n{VALID_LYRIC}"
        )
        review = parse_review_result(
            "# ReviewResult v1\nDECISION: REPAIR\nAFFECTED_LINES: 4\n"
            "SCOPE: LOCAL\nEVIDENCE:\n第4行词序不自然"
        )
        self.assertEqual(supervisor.action, "SEND_GENERATOR")
        self.assertEqual(generation.lyric.lines[3], "阿哥隔岸唱山歌")
        self.assertEqual(review.affected_lines, (4,))

    def test_invalid_contracts_fail_closed(self):
        cases = [
            "说明\nACTION: DELIVER\nMESSAGE:\n完成",
            "# SupervisorResult v1\nACTION: DELIVER\nMESSAGE:\nACTION: SEND_GENERATOR",
            "# ReviewResult v1\nDECISION: APPROVE\nAFFECTED_LINES: 4\n"
            "SCOPE: NONE\nEVIDENCE:\n通过",
            "# ReviewResult v1\nDECISION: REPAIR\nAFFECTED_LINES: NONE\n"
            "SCOPE: LOCAL\nEVIDENCE:\n需要修改",
            "# ReviewResult v1\nDECISION: REPAIR\nAFFECTED_LINES: 17\n"
            "SCOPE: LOCAL\nEVIDENCE:\n越界",
        ]
        parsers = [
            parse_supervisor_result,
            parse_supervisor_result,
            parse_review_result,
            parse_review_result,
            parse_review_result,
        ]
        for parser, case in zip(parsers, cases):
            with self.subTest(case=case), self.assertRaises(ContractError):
                parser(case)

    def test_generation_rejects_markdown_and_extra_text(self):
        with self.assertRaises(ContractError):
            parse_generation_result(f"SUMMARY:\n完成\nLYRIC:\n```text\n{VALID_LYRIC}\n```")
        with self.assertRaises(ContractError):
            parse_generation_result(f"SUMMARY:\n完成\nLYRIC:\n{VALID_LYRIC}\n尾部说明")


class ValidationTests(unittest.TestCase):
    def test_six_hard_gates(self):
        lyric = canonicalize_lyric(VALID_LYRIC)
        result = validate_canonical(lyric, GOLDEN)
        self.assertTrue(result["pass"])
        self.assertEqual(
            set(result["checks"]),
            {
                "exactly_16_lines",
                "four_stanzas_of_four",
                "golden_line_only_at_9_and_13",
                "no_punctuation",
                "no_non_golden_duplicate",
                "no_forbidden_words",
            },
        )

    def test_golden_line_substring_outside_9_and_13_is_blocked(self):
        lyric = canonicalize_lyric(
            VALID_LYRIC.replace("阿哥隔岸唱山歌", f"{GOLDEN}你在哪里")
        )
        result = validate_canonical(lyric, GOLDEN)
        self.assertFalse(result["pass"])
        self.assertEqual(result["golden_line_occurrence_positions"], [4, 9, 13])

    def test_duplicate_gate_surfaces_exact_occurrence_positions(self):
        duplicate = "木槌声声催日落"
        lyric = canonicalize_lyric(
            VALID_LYRIC.replace("山花开满旧村庄", duplicate)
        )
        result = validate_canonical(lyric, GOLDEN)
        self.assertFalse(result["pass"])
        self.assertEqual(result["duplicate_non_golden_lines"], [duplicate])
        self.assertEqual(
            result["duplicate_non_golden_occurrences"],
            [{"text": duplicate, "positions": [3, 16]}],
        )

    def test_frozen_lines_are_code_owned(self):
        before = canonicalize_lyric(VALID_LYRIC)
        good = canonicalize_lyric(VALID_LYRIC.replace("阿哥隔岸唱山歌", "阿哥隔岸把歌唱"))
        bad = canonicalize_lyric(
            VALID_LYRIC.replace("阿哥隔岸唱山歌", "阿哥隔岸把歌唱").replace(
                "山花开满旧村庄", "山花开满小村庄"
            )
        )
        self.assertTrue(validate_local_repair(before, good, [4])["locked_lines_unchanged"])
        self.assertFalse(validate_local_repair(before, bad, [4])["locked_lines_unchanged"])


class StateTests(unittest.TestCase):
    def test_allowed_actions_cannot_be_expanded_by_model(self):
        self.assertEqual(allowed_actions(phase="initial"), ("SEND_GENERATOR", "ASK_HUMAN"))
        self.assertEqual(
            allowed_actions(phase="generated", hard_pass=True),
            ("SEND_REVIEWER", "ASK_HUMAN"),
        )
        self.assertEqual(
            allowed_actions(
                phase="reviewed",
                hard_pass=True,
                review_decision="APPROVE",
                review_scope="NONE",
            ),
            ("DELIVER", "SEND_GENERATOR", "ASK_HUMAN"),
        )
        self.assertEqual(
            allowed_actions(
                phase="reviewed",
                hard_pass=True,
                review_decision="REPAIR",
                review_scope="STRUCTURAL",
            ),
            ("ASK_HUMAN",),
        )

    def test_turn_cas_and_terminal_state(self):
        turn = TurnState(turn_id="t1", role="generator", token="secret")
        turn.transition(TurnStatus.RUNNING, token="secret")
        with self.assertRaises(ValueError):
            turn.transition(TurnStatus.COMPLETED, token="wrong")
        turn.transition(TurnStatus.COMPLETED, token="secret")
        with self.assertRaises(ValueError):
            turn.transition(TurnStatus.INCOMPLETE, token="secret")


class RedactionTests(unittest.TestCase):
    def test_known_secret_forms_are_redacted(self):
        value = "Authorization: Bearer abcdefghijklmnop api_key=secret-value sk-abcdefghijklm"
        redacted = redact_text(value)
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("sk-abcdefghijklm", redacted)


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_replay_and_subscriber_are_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = CaseJournal(Path(directory) / "case-1", "case-1")
            queue = journal.subscribe()
            first = await journal.append("case_created", {"ok": True}, durable=True)
            second = await journal.append("message_completed", {"text": "done"}, durable=True)
            self.assertEqual([first["event_id"], second["event_id"]], [1, 2])
            self.assertEqual([event["event_id"] for event in journal.replay_after(1)], [2])
            self.assertEqual((await queue.get())["event_id"], 1)
            self.assertEqual((await queue.get())["event_id"], 2)
            reloaded = CaseJournal(Path(directory) / "case-1", "case-1")
            self.assertEqual([event["event_id"] for event in reloaded.events], [1, 2])

    async def test_only_truncated_last_record_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case-2"
            journal = CaseJournal(case_dir, "case-2")
            await journal.append("case_created", {}, durable=True)
            with journal.path.open("ab") as handle:
                handle.write(b'{"event_id":')
            recovered = CaseJournal(case_dir, "case-2")
            self.assertEqual(recovered.events[-1]["event_type"], "storage_recovered")
            self.assertEqual(recovered.events[-1]["event_id"], 2)


if __name__ == "__main__":
    unittest.main()
