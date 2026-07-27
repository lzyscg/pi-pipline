from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections import defaultdict, deque
from pathlib import Path

from app.orchestrator import CaseManager
from app.journal import CaseJournal
from app.pi_stream import PiRunResult
from app.server import validate_bind_host


GOLDEN = "我的那个心上人"
V1 = """山风吹过青石坡
阿妹河边洗衣裳
木槌声声催日落
心里头想那个我的郎

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
V2 = V1.replace("心里头想那个我的郎", "心里想起我的情郎")
HARD_BAD = V1.replace(GOLDEN, f"{GOLDEN}呀")
DUPLICATE_BAD = V2.replace("山花开满旧村庄", "木槌声声催日落")
VALID_AGENT_SELECTION = {
    "supervisor": {"model": "opencode/deepseek-v4-flash", "thinking": "medium"},
    "generator": {"model": "opencode/deepseek-v4-pro", "thinking": "high"},
    "reviewer": {"model": "opencode/deepseek-v4-flash", "thinking": "low"},
}
MODEL_CATALOG = {
    "models": [
        {
            "provider": "opencode",
            "model": "deepseek-v4-pro",
            "model_id": "opencode/deepseek-v4-pro",
            "thinking": True,
            "configured": True,
        },
        {
            "provider": "opencode",
            "model": "deepseek-v4-flash",
            "model_id": "opencode/deepseek-v4-flash",
            "thinking": True,
            "configured": True,
        },
    ],
    "configured_providers": ["opencode"],
    "thinking_levels": ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
}


class StaticCatalog:
    def snapshot(self):
        return MODEL_CATALOG


def sup(action: str, message: str = "继续") -> str:
    return f"# SupervisorResult v1\nACTION: {action}\nMESSAGE:\n{message}"


def gen(lyric: str) -> str:
    return f"# GenerationResult v1\nSUMMARY:\n完成歌词\nLYRIC:\n{lyric}"


def review(decision: str, lines: str, scope: str, evidence: str) -> str:
    return (
        "# ReviewResult v1\n"
        f"DECISION: {decision}\nAFFECTED_LINES: {lines}\nSCOPE: {scope}\n"
        f"EVIDENCE:\n{evidence}"
    )


class ScriptedRunner:
    def __init__(self, responses: dict[str, list[str | PiRunResult]]) -> None:
        self.responses = {role: deque(items) for role, items in responses.items()}
        self.calls = defaultdict(list)
        self.stopped = []

    async def run(self, **kwargs):
        role = kwargs["role"]
        self.calls[role].append(kwargs)
        await kwargs["emit"]("thinking_delta", {"text": "diagnostic"})
        item = self.responses[role].popleft()
        if isinstance(item, PiRunResult):
            return item
        await kwargs["emit"]("text_delta", {"text": item})
        return PiRunResult("succeeded", item, kwargs["session_id"], 0)

    async def stop(self, token: str) -> bool:
        self.stopped.append(token)
        return True


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = defaultdict(list)

    async def run(self, **kwargs):
        self.calls[kwargs["role"]].append(kwargs)
        await kwargs["emit"]("thinking_delta", {"text": "partial diagnostic"})
        self.started.set()
        await self.release.wait()
        return PiRunResult("succeeded", sup("SEND_GENERATOR"), kwargs["session_id"], 0)

    async def stop(self, token: str) -> bool:
        self.release.set()
        return True


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def payload(self):
        return {
            "reference_lyrics": "河边洗衣隔岸对歌",
            "golden_line": GOLDEN,
            "style": "山歌民歌",
            "requirements": "",
            "forbidden_words": "",
            "max_repairs": 3,
        }

    async def test_case_persists_and_restores_role_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory, model_catalog=StaticCatalog())
            runner = BlockingRunner()
            manager.runner = runner
            case = await manager.create_case(
                {**self.payload(), "agent_config": VALID_AGENT_SELECTION}
            )
            await runner.started.wait()
            state = case.public_state()
            stored = json.loads((case.case_dir / "input.json").read_text(encoding="utf-8"))
            provenance = json.loads(
                (case.case_dir / "provenance.json").read_text(encoding="utf-8")
            )

            self.assertEqual(state["agent_config"], VALID_AGENT_SELECTION)
            self.assertEqual(state["agent_config_source"], "case")
            self.assertEqual(stored["agent_config"], VALID_AGENT_SELECTION)
            self.assertEqual(provenance["agent_config"], VALID_AGENT_SELECTION)

            await manager.cancel_case(case.case_id)
            await case.task
            reloaded = CaseManager(directory, model_catalog=StaticCatalog())
            restored = reloaded.cases[case.case_id]
            self.assertEqual(
                restored.role_profiles["generator"].model,
                "opencode/deepseek-v4-pro",
            )
            self.assertEqual(restored.public_state()["agent_config_source"], "case")

    async def test_invocation_uses_case_profile_and_records_model_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory, model_catalog=StaticCatalog())
            runner = BlockingRunner()
            manager.runner = runner
            case = await manager.create_case(
                {**self.payload(), "agent_config": VALID_AGENT_SELECTION}
            )
            await runner.started.wait()

            call = runner.calls["supervisor"][0]
            actual = next(
                event
                for event in case.journal.events
                if event["event_type"] == "actual_model_input"
            )
            self.assertEqual(
                call["role_profile"].model,
                VALID_AGENT_SELECTION["supervisor"]["model"],
            )
            self.assertEqual(
                actual["payload"]["model"],
                VALID_AGENT_SELECTION["supervisor"]["model"],
            )
            self.assertEqual(
                actual["payload"]["thinking"],
                VALID_AGENT_SELECTION["supervisor"]["thinking"],
            )

            await manager.cancel_case(case.case_id)
            await case.task

    async def test_direct_natural_business_outputs_deliver(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            manager.runner = ScriptedRunner(
                {
                    "supervisor": [
                        "已整理好本次物料\nSEND_GENERATOR\n请创作完整歌词",
                        "独立冷审已通过\nDELIVER\n可以交付",
                    ],
                    "generator": [V2],
                    "reviewer": ["通过\n16行均无发布阻断问题"],
                }
            )
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "delivered")
            self.assertEqual(case.final_lyric, V2)
            self.assertTrue(case.hard_validation["pass"])
            completed = [e for e in case.journal.events if e["event_type"] == "message_completed"]
            self.assertEqual(
                [e["payload"]["role"] for e in completed],
                ["supervisor", "generator", "reviewer", "supervisor"],
            )
            normalized = [
                e for e in case.journal.events
                if e["event_type"] == "business_output_normalized"
            ]
            self.assertEqual(len(normalized), 4)
            routes = [
                (event["payload"]["source"], event["payload"]["target"])
                for event in case.journal.events
                if event["event_type"] == "route"
            ]
            self.assertEqual(
                routes,
                [
                    ("user", "supervisor"),
                    ("supervisor", "generator"),
                    ("generator", "reviewer"),
                    ("reviewer", "supervisor"),
                    ("supervisor", "delivery"),
                ],
            )

    async def test_neg_ph009_l4_repairs_only_line4_and_cold_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            runner = ScriptedRunner(
                {
                    "supervisor": [
                        sup("SEND_GENERATOR"),
                        sup("DELIVER"),
                    ],
                    "generator": [gen(V1), gen(V2)],
                    "reviewer": [
                        "# ReviewResult v1\nDECISION: REPAIR\nAFFECTED_LINES: 4\n"
                        "SCOPE: LOCAL\nEVIDENCE: 第4行词序不自然",
                        review("APPROVE", "NONE", "NONE", "问题已关闭"),
                    ],
                }
            )
            manager.runner = runner
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "delivered")
            self.assertEqual(case.repair_count, 1)
            repair = case.hard_validation["repair_scope"]
            self.assertEqual(repair["changed_lines"], [4])
            self.assertTrue(repair["locked_lines_unchanged"])
            reviewer_sessions = [call["session_id"] for call in runner.calls["reviewer"]]
            self.assertEqual(len(set(reviewer_sessions)), 2)
            generator_sessions = [call["session_id"] for call in runner.calls["generator"]]
            self.assertEqual(len(set(generator_sessions)), 1)
            self.assertNotIn("心里头想那个我的郎", runner.calls["reviewer"][1]["task_prompt"])
            self.assertNotIn("diagnostic", runner.calls["reviewer"][1]["task_prompt"])
            completed = [
                event["payload"]["role"]
                for event in case.journal.events
                if event["event_type"] == "message_completed"
            ]
            self.assertEqual(
                completed,
                ["supervisor", "generator", "reviewer", "generator", "reviewer", "supervisor"],
            )
            self.assertTrue(
                any(
                    event["event_type"] == "route"
                    and event["payload"]["source"] == "reviewer"
                    and event["payload"]["target"] == "generator"
                    for event in case.journal.events
                )
            )

    async def test_partial_and_illegal_contract_never_route(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            manager.runner = ScriptedRunner(
                {
                    "supervisor": [sup("SEND_GENERATOR")],
                    "generator": [
                        PiRunResult("ambiguous", "# GenerationResult v1\nSUMMARY:\n半截", "s", None)
                    ],
                }
            )
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "waiting_human")
            reviewer_routes = [
                e for e in case.journal.events
                if e["event_type"] == "route" and e["payload"]["target"] == "reviewer"
            ]
            self.assertEqual(reviewer_routes, [])

    async def test_missing_business_semantics_never_route(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            manager.runner = ScriptedRunner(
                {
                    "supervisor": [sup("SEND_GENERATOR")],
                    "generator": ["我已经完成了，请查看"],
                }
            )
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "waiting_human")
            invalid = [
                event
                for event in case.journal.events
                if event["event_type"] == "semantic_output_invalid"
            ]
            self.assertEqual(len(invalid), 1)
            self.assertEqual(invalid[0]["payload"]["role"], "generator")
            self.assertFalse(
                any(
                    event["event_type"] == "route"
                    and event["payload"]["target"] == "reviewer"
                    for event in case.journal.events
                )
            )

    async def test_known_failure_retries_once_with_same_turn_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            known = PiRunResult("known_failed", "", "s", 1, "provider error")
            runner = ScriptedRunner(
                {
                    "supervisor": [known, sup("SEND_GENERATOR"), sup("DELIVER")],
                    "generator": [gen(V2)],
                    "reviewer": [review("APPROVE", "NONE", "NONE", "通过")],
                }
            )
            manager.runner = runner
            case = await manager.create_case(self.payload())
            await case.task
            attempts = [e for e in case.journal.events if e["event_type"] == "attempt_started"]
            first_turn = attempts[0]["turn_id"]
            self.assertEqual(len([e for e in attempts if e["turn_id"] == first_turn]), 2)
            self.assertEqual(runner.calls["supervisor"][0]["session_id"], runner.calls["supervisor"][1]["session_id"])

    async def test_hard_gate_repair_returns_directly_to_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            runner = ScriptedRunner(
                {
                    "supervisor": [sup("SEND_GENERATOR"), sup("DELIVER")],
                    "generator": [gen(HARD_BAD), gen(V1)],
                    "reviewer": [review("APPROVE", "NONE", "NONE", "通过")],
                }
            )
            manager.runner = runner
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "delivered")
            completed = [
                event["payload"]["role"]
                for event in case.journal.events
                if event["event_type"] == "message_completed"
            ]
            self.assertEqual(
                completed,
                ["supervisor", "generator", "generator", "reviewer", "supervisor"],
            )
            self.assertEqual(len(runner.calls["supervisor"]), 2)
            self.assertEqual(len(runner.calls["reviewer"]), 1)
            self.assertTrue(
                any(
                    event["event_type"] == "route"
                    and event["payload"]["source"] == "hard_gate"
                    and event["payload"]["target"] == "generator"
                    for event in case.journal.events
                )
            )

    async def test_duplicate_hard_gate_repairs_only_later_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            runner = ScriptedRunner(
                {
                    "supervisor": [sup("SEND_GENERATOR"), sup("DELIVER")],
                    "generator": [gen(DUPLICATE_BAD), gen(V2)],
                    "reviewer": [review("APPROVE", "NONE", "NONE", "通过")],
                }
            )
            manager.runner = runner
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "delivered")
            repair_route = next(
                event
                for event in case.journal.events
                if event["event_type"] == "route"
                and event["payload"]["source"] == "hard_gate"
            )
            self.assertIn("仅允许修改第 16 行", repair_route["payload"]["message"])
            self.assertEqual(case.hard_validation["repair_scope"]["allowed_lines"], [16])
            self.assertEqual(case.hard_validation["repair_scope"]["changed_lines"], [16])
            self.assertTrue(case.hard_validation["repair_scope"]["locked_lines_unchanged"])

    async def test_illegal_route_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            runner = ScriptedRunner({"supervisor": [sup("DELIVER")]})
            manager.runner = runner
            case = await manager.create_case(self.payload())
            await case.task
            self.assertEqual(case.status.value, "waiting_human")
            self.assertFalse(runner.calls["generator"])
            self.assertTrue(
                any(event["event_type"] == "route_rejected" for event in case.journal.events)
            )

    async def test_cancel_cas_discards_late_completed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaseManager(directory)
            runner = BlockingRunner()
            manager.runner = runner
            case = await manager.create_case(self.payload())
            await runner.started.wait()
            await manager.cancel_case(case.case_id)
            await case.task
            self.assertEqual(case.status.value, "cancelled")
            self.assertTrue(all(turn.status.value == "cancelled" for turn in case.turns.values()))
            self.assertFalse(
                any(event["event_type"] == "message_completed" for event in case.journal.events)
            )

    async def test_running_case_recovers_as_orphaned_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_id = "case-20260724-000000-test00"
            case_dir = root / case_id
            case_dir.mkdir()
            (case_dir / "lyrics").mkdir()
            (case_dir / "input.json").write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "input": self.payload(),
                        "max_repairs": 3,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            journal = CaseJournal(case_dir, case_id)
            await journal.append(
                "case_state",
                {
                    "status": "running",
                    "phase": "generated",
                    "content_version": 0,
                    "repair_count": 0,
                    "session_ids": {"supervisor": "sup-session"},
                },
                status="running",
                durable=True,
            )
            await journal.append(
                "turn_started",
                {"role": "supervisor", "session_id": "sup-session"},
                turn_id="turn-orphan",
                status="running",
                durable=True,
            )
            recovered = CaseManager(directory)
            case = recovered.cases[case_id]
            self.assertEqual(case.status.value, "waiting_human")
            self.assertEqual(case.phase, "orphaned")
            self.assertEqual(case.public_state()["agent_config_source"], "default")
            self.assertEqual(
                case.public_state()["agent_config"]["generator"]["model"],
                "opencode/deepseek-v4-flash",
            )
            await recovered.recover_orphans()
            notice = [
                event for event in case.journal.events
                if event["event_type"] == "orphaned_recovered"
            ]
            self.assertEqual(notice[0]["turn_id"], "turn-orphan")
            self.assertIn("结果可能未知", notice[0]["payload"]["warning"])


class BindTests(unittest.TestCase):
    def test_only_loopback_is_accepted(self):
        self.assertEqual(validate_bind_host("127.0.0.1"), "127.0.0.1")
        with self.assertRaises(RuntimeError):
            validate_bind_host("0.0.0.0")

    def test_golden_position_failure_has_deterministic_repair_lines(self):
        validation = {
            "checks": {"golden_line_only_at_9_and_13": False},
            "gold_positions": [],
            "punctuation_lines": [],
            "forbidden_word_hits": [],
            "duplicate_non_golden_lines": [],
        }
        self.assertEqual(CaseManager._hard_failure_lines(validation), [9, 13])

    def test_duplicate_failure_preserves_first_and_repairs_later_occurrences(self):
        validation = {
            "checks": {"golden_line_only_at_9_and_13": False},
            "gold_positions": [7],
            "golden_line_occurrence_positions": [7],
            "punctuation_lines": [],
            "forbidden_word_hits": [],
            "duplicate_non_golden_lines": ["重复句"],
            "duplicate_non_golden_occurrences": [
                {"text": "重复句", "positions": [3, 15]},
            ],
        }
        self.assertEqual(
            CaseManager._hard_failure_lines(validation),
            [7, 9, 13, 15],
        )

    def test_duplicate_failure_without_positions_fails_closed(self):
        validation = {
            "checks": {"golden_line_only_at_9_and_13": True},
            "punctuation_lines": [4],
            "forbidden_word_hits": [],
            "duplicate_non_golden_lines": ["重复句"],
            "duplicate_non_golden_occurrences": [],
        }
        self.assertEqual(CaseManager._hard_failure_lines(validation), [])


if __name__ == "__main__":
    unittest.main()
