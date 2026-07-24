"""Fixed three-role, supervisor-gated Lite runtime."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .canonical import CanonicalLyric, normalize_golden_line, normalize_text
from .config import LiteProfile, load_profile
from .contracts import (
    ContractError,
    GenerationResult,
    ReviewResult,
    parse_generation_result,
    parse_review_result,
    parse_supervisor_result,
)
from .journal import CaseJournal
from .pi_stream import PiRunResult, PiStreamRunner
from .prompts import SYSTEM_PROMPTS, generator_prompt, reviewer_prompt, supervisor_prompt
from .provenance import LITE_ROOT, collect_provenance, ensure_private_runtime_dir, write_provenance
from .redaction import redact_text
from .state import (
    CASE_TRANSITIONS,
    CaseStatus,
    TurnState,
    TurnStatus,
    allowed_actions,
    require_transition,
)
from .validation_adapter import validate_canonical, validate_local_repair


class CasePaused(RuntimeError):
    pass


@dataclass(slots=True)
class CaseRuntime:
    case_id: str
    case_dir: Path
    input: dict
    max_repairs: int
    journal: CaseJournal
    status: CaseStatus = CaseStatus.CREATED
    phase: str = "initial"
    content_version: int = 0
    repair_count: int = 0
    lyrics: dict[int, CanonicalLyric] = field(default_factory=dict)
    hard_validation: dict | None = None
    review: ReviewResult | None = None
    review_id: str | None = None
    session_ids: dict[str, str] = field(default_factory=dict)
    turns: dict[str, TurnState] = field(default_factory=dict)
    active_turn_id: str | None = None
    active_token: str | None = None
    current_role: str | None = None
    final_lyric: str | None = None
    recovery_notice: dict | None = None
    task: asyncio.Task | None = None
    role_locks: dict[str, asyncio.Lock] = field(
        default_factory=lambda: {role: asyncio.Lock() for role in ("supervisor", "generator", "reviewer")}
    )

    def public_state(self) -> dict:
        return {
            "case_id": self.case_id,
            "task_title": self.input.get("golden_line") or "未命名歌词任务",
            "task_style": self.input.get("style") or "未指定风格",
            "status": self.status.value,
            "phase": self.phase,
            "content_version": self.content_version,
            "repair_count": self.repair_count,
            "turn_count": len(
                {
                    event["turn_id"]
                    for event in self.journal.events
                    if event["event_type"] == "turn_started" and event.get("turn_id")
                }
            ),
            "rounds": self._round_summaries(),
            "max_repairs": self.max_repairs,
            "current_role": self.current_role,
            "session_ids": self.session_ids,
            "hard_validation": self.hard_validation,
            "review": asdict(self.review) if self.review else None,
            "blocking": self._blocking_state(),
            "lyrics": {str(k): v.text for k, v in self.lyrics.items()},
            "final_lyric": self.final_lyric,
            "last_event_id": self.journal.events[-1]["event_id"] if self.journal.events else 0,
        }

    def _blocking_state(self) -> dict | None:
        if self.status != CaseStatus.WAITING_HUMAN:
            return None
        event = next(
            (
                item
                for item in reversed(self.journal.events)
                if item["event_type"] == "waiting_human"
            ),
            None,
        )
        if event is None:
            return {
                "code": "waiting_human",
                "reason": "任务正在等待人工处理",
                "details": {},
                "event_id": None,
            }
        payload = event.get("payload") or {}
        reason = payload.get("reason") or "任务正在等待人工处理"
        code = payload.get("code") or "waiting_human"
        if reason == "硬门失败且无法安全自动返修或预算耗尽":
            if self.repair_count >= self.max_repairs:
                code = "repair_budget_exhausted"
                reason = "代码硬门仍未通过，自动返修预算已耗尽"
            else:
                code = "hard_gate_no_safe_scope"
                reason = "代码硬门未通过，旧校验记录没有提供可安全自动返修的行号"
        return {
            "code": code,
            "reason": reason,
            "details": payload.get("details") or {},
            "event_id": event.get("event_id"),
        }

    def _round_summaries(self) -> list[dict]:
        rounds: dict[int, dict] = {}
        for event in self.journal.events:
            version = int(event.get("content_version") or 0)
            payload = event.get("payload") or {}
            if event["event_type"] == "lyric_version":
                version = int(payload["version"])
                hard_pass = bool((payload.get("validation") or {}).get("pass"))
                rounds[version] = {
                    "version": version,
                    "status": "待审核" if hard_pass else "硬门打回",
                    "hard_pass": hard_pass,
                    "review_decision": None,
                }
            elif event["event_type"] == "review_completed" and version:
                item = rounds.setdefault(
                    version,
                    {
                        "version": version,
                        "status": "已审核",
                        "hard_pass": True,
                        "review_decision": None,
                    },
                )
                item["review_decision"] = payload.get("decision")
                item["status"] = "审核通过" if payload.get("decision") == "APPROVE" else "审核打回"
        if self.status == CaseStatus.DELIVERED and self.content_version in rounds:
            rounds[self.content_version]["status"] = "已交付"
        return [rounds[key] for key in sorted(rounds)]


class CaseManager:
    def __init__(self, data_dir: str | None = None) -> None:
        self.root = ensure_private_runtime_dir(data_dir)
        self.profile_path = LITE_ROOT / "profiles" / "mountain-song.json"
        self.profile: LiteProfile = load_profile(self.profile_path)
        self.provenance = collect_provenance(self.profile_path, self.profile)
        self.runner = PiStreamRunner()
        self.cases: dict[str, CaseRuntime] = {}
        self._manager_lock = asyncio.Lock()
        self._load_existing()

    def _load_existing(self) -> None:
        for case_dir in sorted(self.root.glob("case-*"), reverse=True)[:10]:
            meta = case_dir / "input.json"
            if not meta.exists():
                continue
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                journal = CaseJournal(case_dir, raw["case_id"])
                state_events = [e for e in journal.events if e["event_type"] == "case_state"]
                state = state_events[-1]["payload"] if state_events else {}
                case = CaseRuntime(
                    case_id=raw["case_id"],
                    case_dir=case_dir,
                    input=raw["input"],
                    max_repairs=raw["max_repairs"],
                    journal=journal,
                    status=CaseStatus(state.get("status", "failed")),
                    phase=state.get("phase", "waiting_human"),
                    content_version=state.get("content_version", 0),
                    repair_count=state.get("repair_count", 0),
                    final_lyric=state.get("final_lyric"),
                    session_ids=state.get("session_ids", {}),
                    hard_validation=state.get("hard_validation"),
                    review=self._restore_review(state.get("review")),
                )
                for path in (case_dir / "lyrics").glob("v*.txt"):
                    from .canonical import canonicalize_lyric
                    version = int(path.stem[1:])
                    case.lyrics[version] = canonicalize_lyric(path.read_text(encoding="utf-8"))
                if case.status == CaseStatus.RUNNING:
                    case.status = CaseStatus.WAITING_HUMAN
                    case.phase = "orphaned"
                    running_turns = [
                        event
                        for event in journal.events
                        if event["event_type"] == "turn_started"
                        and not any(
                            later["turn_id"] == event["turn_id"]
                            and later["event_type"] in {
                                "message_completed",
                                "turn_terminal",
                                "contract_invalid",
                                "semantic_output_invalid",
                            }
                            for later in journal.events
                            if later["event_id"] > event["event_id"]
                        )
                    ]
                    last_turn = running_turns[-1] if running_turns else None
                    case.recovery_notice = {
                        "turn_id": last_turn["turn_id"] if last_turn else None,
                        "session_id": (last_turn or {}).get("payload", {}).get("session_id"),
                        "warning": "最后一小段流式诊断可能缺失，结果可能未知；仅保留最后 completed 业务内容",
                    }
                self.cases[case.case_id] = case
            except Exception:
                continue

    @staticmethod
    def _restore_review(raw: dict | None) -> ReviewResult | None:
        if not raw:
            return None
        return ReviewResult(
            decision=raw["decision"],
            affected_lines=tuple(raw.get("affected_lines") or ()),
            scope=raw["scope"],
            evidence=raw["evidence"],
        )

    async def recover_orphans(self) -> None:
        for case in self.cases.values():
            if not case.recovery_notice:
                continue
            await case.journal.append(
                "orphaned_recovered",
                case.recovery_notice,
                turn_id=case.recovery_notice.get("turn_id"),
                status="orphaned",
                durable=True,
            )
            await self._state(case)
            case.recovery_notice = None

    @staticmethod
    def _transition_case(case: CaseRuntime, target: CaseStatus) -> None:
        require_transition(case.status, target, CASE_TRANSITIONS)
        case.status = target

    async def create_case(self, payload: dict) -> CaseRuntime:
        async with self._manager_lock:
            if any(c.status == CaseStatus.RUNNING for c in self.cases.values()):
                raise ValueError("Lite 同时只允许运行一个 Case")
            input_data = {
                "reference_lyrics": normalize_text(
                    str(payload.get("reference_lyrics", "")),
                    field_name="参考歌词",
                    max_bytes=128 * 1024,
                ).strip(),
                "golden_line": normalize_golden_line(str(payload.get("golden_line", ""))),
                "style": normalize_text(
                    str(payload.get("style", "山歌民歌")),
                    field_name="风格",
                    max_bytes=2048,
                ).strip(),
                "requirements": normalize_text(
                    str(payload.get("requirements", "")),
                    field_name="补充要求",
                    max_bytes=8192,
                ).strip(),
                "forbidden_words": normalize_text(
                    str(payload.get("forbidden_words", "")),
                    field_name="禁用词",
                    max_bytes=4096,
                ).strip(),
            }
            if not input_data["reference_lyrics"]:
                raise ValueError("参考歌词或素材不能为空")
            max_repairs = int(payload.get("max_repairs", 3))
            if max_repairs not in {2, 3}:
                raise ValueError("返修轮数只能为 2 或 3")
            case_id = f"case-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
            case_dir = self.root / case_id
            case_dir.mkdir(mode=0o700)
            (case_dir / "lyrics").mkdir(mode=0o700)
            (case_dir / "pi_sessions").mkdir(mode=0o700)
            journal = CaseJournal(case_dir, case_id)
            case = CaseRuntime(case_id, case_dir, input_data, max_repairs, journal)
            case.session_ids = {
                "supervisor": f"{case_id}__supervisor",
                "generator": f"{case_id}__generator",
            }
            meta = {"case_id": case_id, "input": input_data, "max_repairs": max_repairs}
            (case_dir / "input.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            (case_dir / "input.json").chmod(0o600)
            write_provenance(case_dir / "provenance.json", self.provenance)
            self.cases[case_id] = case
            await journal.append("case_created", meta, status="created", durable=True)
            self._transition_case(case, CaseStatus.RUNNING)
            await self._state(case)
            case.task = asyncio.create_task(self._run_case(case))
            return case

    async def _state(self, case: CaseRuntime) -> None:
        state = case.public_state()
        await case.journal.append("case_state", state, status=case.status.value, durable=True)
        case.journal.write_snapshot(state)

    async def _run_case(self, case: CaseRuntime) -> None:
        try:
            await self._business_event(case, "user", "supervisor", "normal", json.dumps(case.input, ensure_ascii=False))
            sup = await self._supervisor(case, "initial", json.dumps(case.input, ensure_ascii=False))
            if sup.action == "ASK_HUMAN":
                await self._wait_human(case, "总控请求人工处理")
                return
            if sup.action != "SEND_GENERATOR":
                await self._wait_human(case, "总控初始路由无效")
                return
            generation_instruction = sup.message
            await self._route(case, "supervisor", "generator", "normal", generation_instruction)

            while case.status == CaseStatus.RUNNING:
                generation = await self._call_and_parse(
                    case,
                    "generator",
                    self._generator_task(case, generation_instruction),
                )
                if generation is None:
                    return
                assert isinstance(generation, GenerationResult)
                previous = case.lyrics.get(case.content_version)
                case.content_version += 1
                case.lyrics[case.content_version] = generation.lyric
                lyric_path = case.case_dir / "lyrics" / f"v{case.content_version}.txt"
                lyric_path.write_text(generation.lyric.text + "\n", encoding="utf-8")
                lyric_path.chmod(0o600)
                case.hard_validation = validate_canonical(
                    generation.lyric,
                    case.input["golden_line"],
                    case.input["forbidden_words"],
                )
                if previous:
                    repair = validate_local_repair(previous, generation.lyric, self._repair_lines(case))
                    case.hard_validation["repair_scope"] = repair
                    if not repair["locked_lines_unchanged"]:
                        case.hard_validation["pass"] = False
                    case.repair_count += 1
                await case.journal.append(
                    "lyric_version",
                    {
                        "version": case.content_version,
                        "lyric": generation.lyric.text,
                        "validation": case.hard_validation,
                    },
                    content_version=case.content_version,
                    status="completed",
                    durable=True,
                )

                # Code hard gates run before review. A rejected artifact never
                # reaches another model; the system returns a scoped ticket
                # directly to the persistent generator Session.
                if not case.hard_validation["pass"]:
                    lines = self._hard_failure_lines(case.hard_validation)
                    failed_checks = [
                        name
                        for name, passed in case.hard_validation.get("checks", {}).items()
                        if not passed
                    ]
                    details = {
                        "failed_checks": failed_checks,
                        "repair_count": case.repair_count,
                        "max_repairs": case.max_repairs,
                    }
                    if case.repair_count >= case.max_repairs:
                        await self._wait_human(
                            case,
                            "代码硬门仍未通过，自动返修预算已耗尽",
                            code="repair_budget_exhausted",
                            details=details,
                        )
                        return
                    if not lines:
                        await self._wait_human(
                            case,
                            "代码硬门未通过，但无法从校验结果确定安全的定点返修行",
                            code="hard_gate_no_safe_scope",
                            details=details,
                        )
                        return
                    case.review = ReviewResult(
                        "REPAIR",
                        tuple(lines),
                        "LOCAL",
                        "代码硬门拒绝当前歌词版本",
                    )
                    generation_instruction = self._hard_repair_instruction(case, lines)
                    await self._route(
                        case,
                        "hard_gate",
                        "generator",
                        "repair",
                        generation_instruction,
                    )
                    continue

                # A hard-valid generation goes directly to a fresh cold review
                # Session. The supervisor is not called between these nodes.
                await self._route(
                    case,
                    "generator",
                    "reviewer",
                    "review",
                    f"歌词 v{case.content_version} 已通过代码硬门，请独立冷审当前唯一版本",
                )
                review = await self._call_and_parse(
                    case,
                    "reviewer",
                    f"冷审歌词 v{case.content_version}",
                )
                if review is None:
                    return
                assert isinstance(review, ReviewResult)
                case.review = review
                case.review_id = str(uuid.uuid4())
                await case.journal.append(
                    "review_completed",
                    {
                        "review_id": case.review_id,
                        **asdict(review),
                        "session_id": case.session_ids["reviewer"],
                    },
                    content_version=case.content_version,
                    status="completed",
                    durable=True,
                )

                if review.decision == "REPAIR":
                    if review.scope == "INPUT":
                        await self._wait_human(case, "审核判定输入素材问题，需要人工补充")
                        return
                    if case.repair_count >= case.max_repairs:
                        await self._wait_human(case, "审核打回但自动返修预算已耗尽")
                        return
                    if review.scope == "STRUCTURAL":
                        case.review = ReviewResult(
                            "REPAIR",
                            tuple(range(1, 17)),
                            "STRUCTURAL",
                            review.evidence,
                        )
                    generation_instruction = self._review_repair_instruction(case.review)
                    await self._route(
                        case,
                        "reviewer",
                        "generator",
                        "repair",
                        generation_instruction,
                    )
                    continue

                # Only an APPROVE result reaches the supervisor. The supervisor
                # makes the final business decision, while code retains veto.
                await self._route(
                    case,
                    "reviewer",
                    "supervisor",
                    "approval",
                    f"歌词 v{case.content_version} 冷审通过，提交总控终审",
                )
                sup = await self._supervisor(
                    case,
                    "reviewed",
                    json.dumps(asdict(review), ensure_ascii=False),
                )
                if sup.action == "ASK_HUMAN":
                    await self._wait_human(case, "总控终审请求人工处理")
                    return
                if sup.action == "SEND_GENERATOR":
                    if case.repair_count >= case.max_repairs:
                        await self._wait_human(case, "总控打回但自动返修预算已耗尽")
                        return
                    case.review = ReviewResult(
                        "REPAIR",
                        tuple(range(1, 17)),
                        "STRUCTURAL",
                        sup.message,
                    )
                    generation_instruction = sup.message
                    await self._route(
                        case,
                        "supervisor",
                        "generator",
                        "repair",
                        generation_instruction,
                    )
                    continue
                if sup.action != "DELIVER":
                    await self._wait_human(case, "总控终审路由无效")
                    return
                if not (
                    case.hard_validation
                    and case.hard_validation["pass"]
                    and case.review
                    and case.review.decision == "APPROVE"
                    and case.review.scope == "NONE"
                ):
                    await self._wait_human(case, "代码否决非法交付")
                    return
                case.final_lyric = case.lyrics[case.content_version].text
                self._transition_case(case, CaseStatus.DELIVERED)
                case.phase = "delivered"
                await self._route(case, "supervisor", "delivery", "deliver", case.final_lyric)
                await self._state(case)
                return
        except CasePaused:
            return
        except Exception as exc:
            await case.journal.append("runtime_error", {"summary": redact_text(str(exc))[:1000]}, status="failed", durable=True)
            if case.status not in {CaseStatus.DELIVERED, CaseStatus.CANCELLED, CaseStatus.FAILED}:
                self._transition_case(case, CaseStatus.FAILED)
                case.phase = "failed"
                await self._state(case)

    async def _supervisor(self, case: CaseRuntime, phase: str, business: str):
        review = case.review
        actions = allowed_actions(
            phase=phase,
            hard_pass=case.hard_validation["pass"] if case.hard_validation else None,
            review_decision=review.decision if review else None,
            review_scope=review.scope if review else None,
        )
        envelope = {
            "case_id": case.case_id,
            "phase": phase,
            "content_version": case.content_version,
            "allowed_actions": actions,
            "allowed_lines": self._repair_lines(case),
            "locked_lines": (
                [n for n in range(1, 17) if n not in self._repair_lines(case)]
                if case.content_version and self._repair_lines(case)
                else []
            ),
            "latest_review_id": case.review_id,
        }
        result = await self._call_and_parse(case, "supervisor", supervisor_prompt(envelope, business))
        if result is None:
            raise CasePaused("总控 Turn 未完成")
        if result.action not in actions:
            await case.journal.append("route_rejected", {"action": result.action, "allowed_actions": actions}, status="waiting_human", durable=True)
            await self._wait_human(case, "总控返回不允许的路由")
            raise CasePaused("总控错误路由")
        case.phase = phase
        return result

    def _generator_task(self, case: CaseRuntime, task: str) -> str:
        previous = case.lyrics.get(case.content_version)
        envelope = {
            "case_id": case.case_id,
            "content_version": case.content_version,
            "golden_line": case.input["golden_line"],
            "style": case.input["style"],
            "forbidden_words": case.input["forbidden_words"],
            "allowed_lines": self._repair_lines(case),
            "locked_lines": [n for n in range(1, 17) if n not in self._repair_lines(case)] if previous else [],
            "previous_lyric": previous.text if previous else None,
        }
        return generator_prompt(envelope, task + "\n参考素材：\n" + case.input["reference_lyrics"])

    @staticmethod
    def _review_repair_instruction(review: ReviewResult) -> str:
        line_text = ",".join(str(line) for line in review.affected_lines)
        if review.scope == "STRUCTURAL":
            return f"审核判定需要结构性返修。问题证据：\n{review.evidence}\n允许重写全部16行。"
        return (
            f"审核打回，仅允许修改第 {line_text} 行；其他行逐字冻结。"
            f"\n问题证据：\n{review.evidence}"
        )

    @staticmethod
    def _hard_repair_instruction(case: CaseRuntime, lines: list[int]) -> str:
        line_text = ",".join(str(line) for line in lines)
        return (
            f"代码硬门拒绝歌词 v{case.content_version}。仅允许修改第 {line_text} 行，"
            "其他行逐字冻结。请修复硬门问题后返回完整歌词。"
            f"\n硬门结果：\n{json.dumps(case.hard_validation, ensure_ascii=False)}"
        )

    async def _call_and_parse(self, case: CaseRuntime, role: str, task_prompt: str):
        parser = {"supervisor": parse_supervisor_result, "generator": parse_generation_result, "reviewer": parse_review_result}[role]
        if role == "reviewer":
            session_id = f"{case.case_id}__reviewer__v{case.content_version}__{secrets.token_hex(2)}"
            case.session_ids["reviewer"] = session_id
            task_prompt = reviewer_prompt(
                {"case_id": case.case_id, "content_version": case.content_version, "golden_line": case.input["golden_line"], "hard_validation": case.hard_validation},
                case.lyrics[case.content_version].text,
            )
        else:
            session_id = case.session_ids[role]
        async with case.role_locks[role]:
            turn_id = f"turn-{uuid.uuid4().hex[:10]}"
            token = secrets.token_urlsafe(16)
            turn = TurnState(turn_id, role, token)
            case.turns[turn_id] = turn
            case.active_turn_id, case.active_token, case.current_role = turn_id, token, role
            await case.journal.append("turn_queued", {"role": role}, turn_id=turn_id, status="queued")
            turn.transition(TurnStatus.RUNNING, token=token)
            await case.journal.append("turn_started", {"role": role, "session_id": session_id}, turn_id=turn_id, status="running")
            await case.journal.append(
                "actual_model_input",
                {"role": role, "system_prompt": redact_text(SYSTEM_PROMPTS[role]), "task_prompt": redact_text(task_prompt), "skill": self.profile.roles[role].skill, "session_id": session_id},
                turn_id=turn_id,
                content_version=case.content_version,
            )
            result = await self._invoke_attempts(case, turn, session_id, task_prompt)
            case.current_role = case.active_turn_id = case.active_token = None
            if case.status == CaseStatus.CANCELLED:
                if turn.status == TurnStatus.RUNNING:
                    turn.transition(TurnStatus.CANCELLED, token=token)
                await case.journal.append(
                    "turn_terminal",
                    {
                        "role": role,
                        "attempt_status": result.attempt_status,
                        "partial": result.final_text,
                        "reason": "case_cancelled",
                    },
                    turn_id=turn_id,
                    status="cancelled",
                    durable=True,
                )
                return None
            if result.attempt_status != "succeeded":
                target = TurnStatus.INCOMPLETE if result.attempt_status == "killed" else TurnStatus.ORPHANED if result.attempt_status == "ambiguous" else TurnStatus.FAILED
                turn.transition(target, token=token)
                await case.journal.append("turn_terminal", {"role": role, "attempt_status": result.attempt_status, "partial": result.final_text}, turn_id=turn_id, status=target.value, durable=True)
                await self._wait_human(case, f"{role} 未产生可提交 completed 输出")
                return None
            try:
                parsed = parser(result.final_text)
            except ContractError as exc:
                turn.transition(TurnStatus.FAILED, token=token)
                await case.journal.append(
                    "semantic_output_invalid",
                    {"role": role, "error": str(exc), "raw_final": result.final_text},
                    turn_id=turn_id,
                    status="failed",
                    durable=True,
                )
                await self._wait_human(
                    case,
                    f"{role} 业务输出缺少可确定语义",
                    code="semantic_output_invalid",
                    details={"role": role, "error": str(exc)},
                )
                return None
            turn.transition(TurnStatus.COMPLETED, token=token)
            await case.journal.append(
                "business_output_normalized",
                {"role": role, "adapter": "middleware_semantic_v1", "result": asdict(parsed)},
                turn_id=turn_id,
                content_version=case.content_version,
                status="normalized",
                durable=True,
            )
            await case.journal.append("message_completed", {"role": role, "final_output": result.final_text, "session_id": session_id}, turn_id=turn_id, content_version=case.content_version, status="completed", durable=True)
            return parsed

    async def _invoke_attempts(self, case: CaseRuntime, turn: TurnState, session_id: str, prompt: str) -> PiRunResult:
        for attempt_number in (1, 2):
            attempt_id = f"{turn.turn_id}:a{attempt_number}"
            await case.journal.append("attempt_started", {"number": attempt_number}, turn_id=turn.turn_id, attempt_id=attempt_id, status="started")
            async def emit(kind: str, payload: dict) -> None:
                await case.journal.append(kind, payload, turn_id=turn.turn_id, attempt_id=attempt_id, content_version=case.content_version)
            result = await self.runner.run(
                role=turn.role,
                role_profile=self.profile.roles[turn.role],
                session_dir=case.case_dir / "pi_sessions",
                session_id=session_id,
                system_prompt=SYSTEM_PROMPTS[turn.role],
                task_prompt=prompt,
                token=turn.token,
                emit=emit,
            )
            await case.journal.append("attempt_terminal", {"attempt_status": result.attempt_status, "return_code": result.return_code, "error_summary": result.error_summary}, turn_id=turn.turn_id, attempt_id=attempt_id, status=result.attempt_status, durable=True)
            if result.attempt_status != "known_failed" or attempt_number == 2:
                return result
        raise AssertionError("unreachable")

    async def _route(self, case: CaseRuntime, source: str, target: str, kind: str, message: str) -> None:
        await case.journal.append("route", {"source": source, "target": target, "kind": kind, "message": message}, content_version=case.content_version, durable=True)

    async def _business_event(self, case: CaseRuntime, source: str, target: str, kind: str, message: str) -> None:
        await self._route(case, source, target, kind, message)

    async def _wait_human(
        self,
        case: CaseRuntime,
        reason: str,
        *,
        code: str = "human_required",
        details: dict | None = None,
    ) -> None:
        if case.status == CaseStatus.RUNNING:
            self._transition_case(case, CaseStatus.WAITING_HUMAN)
            case.phase = "waiting_human"
            await case.journal.append(
                "waiting_human",
                {"code": code, "reason": reason, "details": details or {}},
                status="waiting_human",
                durable=True,
            )
            await self._state(case)

    def _repair_lines(self, case: CaseRuntime) -> list[int]:
        return list(case.review.affected_lines) if case.review and case.review.decision == "REPAIR" else []

    @staticmethod
    def _hard_failure_lines(validation: dict) -> list[int]:
        lines = set(validation.get("punctuation_lines", []))
        lines.update(hit["line"] for hit in validation.get("forbidden_word_hits", []))
        if not validation.get("checks", {}).get("golden_line_only_at_9_and_13", True):
            lines.update({9, 13})
            lines.update(validation.get("gold_positions", []))
            lines.update(validation.get("golden_line_occurrence_positions", []))
        duplicates = set(validation.get("duplicate_non_golden_lines", []))
        duplicate_occurrences = validation.get("duplicate_non_golden_occurrences", [])
        if duplicates:
            mapped = {
                item.get("text"): item.get("positions")
                for item in duplicate_occurrences
                if isinstance(item, dict)
            }
            # Preserve the first occurrence and repair only later copies. If
            # the adapter cannot prove every duplicate position, fail closed.
            for duplicate in duplicates:
                positions = mapped.get(duplicate)
                if (
                    not isinstance(positions, list)
                    or len(positions) < 2
                    or positions != sorted(set(positions))
                    or any(not isinstance(number, int) or not 1 <= number <= 16 for number in positions)
                ):
                    return []
                lines.update(positions[1:])
        return sorted(lines)

    async def stop_current(self, case_id: str) -> bool:
        case = self.cases[case_id]
        token = case.active_token
        return bool(token and await self.runner.stop(token))

    async def cancel_case(self, case_id: str) -> None:
        case = self.cases[case_id]
        if case.status in {CaseStatus.DELIVERED, CaseStatus.CANCELLED, CaseStatus.FAILED}:
            raise ValueError("Case 已处于终态")
        token = case.active_token
        active_turn = case.turns.get(case.active_turn_id or "")
        self._transition_case(case, CaseStatus.CANCELLED)
        case.phase = "cancelled"
        if active_turn and active_turn.status == TurnStatus.RUNNING:
            active_turn.transition(TurnStatus.CANCELLED, token=token)
        if token:
            await self.runner.stop(token)
        await self._state(case)

    async def manual_continue(self, case_id: str, target: str, instruction: str) -> None:
        case = self.cases[case_id]
        if case.status != CaseStatus.WAITING_HUMAN:
            raise ValueError("只有 waiting_human Case 可以人工继续")
        if target not in {"supervisor", "generator", "reviewer"}:
            raise ValueError("人工目标 Agent 无效")
        instruction = normalize_text(
            instruction, field_name="人工补充指令", max_bytes=8192
        ).strip()
        if not instruction:
            raise ValueError("人工补充指令不能为空")
        self._transition_case(case, CaseStatus.RUNNING)
        case.phase = "manual_continue"
        await case.journal.append(
            "manual_continue",
            {"target": target, "instruction": instruction, "parent_turn_id": case.active_turn_id},
            status="running",
            durable=True,
        )
        await self._state(case)
        case.task = asyncio.create_task(self._manual_turn(case, target, instruction))

    async def _manual_turn(self, case: CaseRuntime, target: str, instruction: str) -> None:
        try:
            if target == "generator":
                prompt = self._generator_task(case, instruction)
            elif target == "reviewer" and case.content_version:
                prompt = instruction
            elif target == "supervisor":
                actions = ("ASK_HUMAN",)
                prompt = supervisor_prompt(
                    {
                        "case_id": case.case_id,
                        "phase": "manual",
                        "content_version": case.content_version,
                        "allowed_actions": actions,
                    },
                    instruction,
                )
            else:
                await self._wait_human(case, "当前没有可供审核的歌词版本")
                return
            await self._call_and_parse(case, target, prompt)
            await self._wait_human(case, "人工 Turn 已完成，请检查输出后决定后续动作")
        except Exception as exc:
            await self._wait_human(case, f"人工 Turn 失败：{redact_text(str(exc))[:500]}")

    def recent(self) -> list[dict]:
        return [case.public_state() for case in sorted(self.cases.values(), key=lambda c: c.case_id, reverse=True)[:10]]
