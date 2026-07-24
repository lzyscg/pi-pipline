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
from .state import CaseStatus, TurnState, TurnStatus, allowed_actions
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
    task: asyncio.Task | None = None
    role_locks: dict[str, asyncio.Lock] = field(
        default_factory=lambda: {role: asyncio.Lock() for role in ("supervisor", "generator", "reviewer")}
    )

    def public_state(self) -> dict:
        return {
            "case_id": self.case_id,
            "status": self.status.value,
            "phase": self.phase,
            "content_version": self.content_version,
            "repair_count": self.repair_count,
            "max_repairs": self.max_repairs,
            "current_role": self.current_role,
            "session_ids": self.session_ids,
            "hard_validation": self.hard_validation,
            "review": asdict(self.review) if self.review else None,
            "lyrics": {str(k): v.text for k, v in self.lyrics.items()},
            "final_lyric": self.final_lyric,
            "last_event_id": self.journal.events[-1]["event_id"] if self.journal.events else 0,
        }


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
                )
                for path in (case_dir / "lyrics").glob("v*.txt"):
                    from .canonical import canonicalize_lyric
                    version = int(path.stem[1:])
                    case.lyrics[version] = canonicalize_lyric(path.read_text(encoding="utf-8"))
                if case.status == CaseStatus.RUNNING:
                    case.status = CaseStatus.WAITING_HUMAN
                    case.phase = "orphaned"
                self.cases[case.case_id] = case
            except Exception:
                continue

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
            case.status = CaseStatus.RUNNING
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
            while case.status == CaseStatus.RUNNING:
                if sup.action == "ASK_HUMAN":
                    await self._wait_human(case, "总控请求人工处理")
                    return
                if sup.action == "SEND_GENERATOR":
                    await self._route(case, "supervisor", "generator", "repair" if case.content_version else "normal", sup.message)
                    generation = await self._call_and_parse(case, "generator", self._generator_task(case, sup.message))
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
                        {"version": case.content_version, "lyric": generation.lyric.text, "validation": case.hard_validation},
                        content_version=case.content_version,
                        status="completed",
                        durable=True,
                    )
                    if not case.hard_validation["pass"]:
                        lines = self._hard_failure_lines(case.hard_validation)
                        if not lines or case.repair_count >= case.max_repairs:
                            await self._wait_human(case, "硬门失败且无法安全自动返修或预算耗尽")
                            return
                        case.review = ReviewResult("REPAIR", tuple(lines), "LOCAL", "代码硬校验失败")
                        sup = await self._supervisor(case, "generated", json.dumps(case.hard_validation, ensure_ascii=False))
                        continue
                    sup = await self._supervisor(case, "generated", f"歌词 v{case.content_version} 已通过硬门")
                    continue
                if sup.action == "SEND_REVIEWER":
                    await self._route(case, "supervisor", "reviewer", "normal", sup.message)
                    review = await self._call_and_parse(case, "reviewer", sup.message)
                    if review is None:
                        return
                    assert isinstance(review, ReviewResult)
                    case.review = review
                    case.review_id = str(uuid.uuid4())
                    await case.journal.append(
                        "review_completed",
                        {"review_id": case.review_id, **asdict(review), "session_id": case.session_ids["reviewer"]},
                        content_version=case.content_version,
                        status="completed",
                        durable=True,
                    )
                    sup = await self._supervisor(case, "reviewed", json.dumps(asdict(review), ensure_ascii=False))
                    continue
                if sup.action == "DELIVER":
                    if not (case.hard_validation and case.hard_validation["pass"] and case.review and case.review.decision == "APPROVE"):
                        await self._wait_human(case, "代码否决非法交付")
                        return
                    case.final_lyric = case.lyrics[case.content_version].text
                    case.status = CaseStatus.DELIVERED
                    case.phase = "delivered"
                    await self._route(case, "supervisor", "delivery", "deliver", case.final_lyric)
                    await self._state(case)
                    return
                await self._wait_human(case, "未知流程状态")
                return
        except CasePaused:
            return
        except Exception as exc:
            await case.journal.append("runtime_error", {"summary": redact_text(str(exc))[:1000]}, status="failed", durable=True)
            case.status = CaseStatus.FAILED
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
            "locked_lines": [n for n in range(1, 17) if n not in self._repair_lines(case)],
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
                await case.journal.append("contract_invalid", {"role": role, "error": str(exc), "raw_final": result.final_text}, turn_id=turn_id, status="failed", durable=True)
                await self._wait_human(case, f"{role} 输出合同非法")
                return None
            turn.transition(TurnStatus.COMPLETED, token=token)
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

    async def _wait_human(self, case: CaseRuntime, reason: str) -> None:
        if case.status == CaseStatus.RUNNING:
            case.status = CaseStatus.WAITING_HUMAN
            case.phase = "waiting_human"
            await case.journal.append("waiting_human", {"reason": reason}, status="waiting_human", durable=True)
            await self._state(case)

    def _repair_lines(self, case: CaseRuntime) -> list[int]:
        return list(case.review.affected_lines) if case.review and case.review.decision == "REPAIR" else []

    @staticmethod
    def _hard_failure_lines(validation: dict) -> list[int]:
        lines = set(validation.get("punctuation_lines", []))
        lines.update(hit["line"] for hit in validation.get("forbidden_word_hits", []))
        duplicates = set(validation.get("duplicate_non_golden_lines", []))
        # Duplicate values are surfaced without positions by the legacy gate;
        # no safe local scope can be inferred from them.
        if duplicates and not lines:
            return []
        return sorted(lines)

    async def stop_current(self, case_id: str) -> bool:
        case = self.cases[case_id]
        token = case.active_token
        return bool(token and await self.runner.stop(token))

    async def cancel_case(self, case_id: str) -> None:
        case = self.cases[case_id]
        if case.status in {CaseStatus.DELIVERED, CaseStatus.CANCELLED, CaseStatus.FAILED}:
            raise ValueError("Case 已处于终态")
        await self.stop_current(case_id)
        case.status = CaseStatus.CANCELLED
        case.phase = "cancelled"
        await self._state(case)

    def recent(self) -> list[dict]:
        return [case.public_state() for case in sorted(self.cases.values(), key=lambda c: c.case_id, reverse=True)[:10]]
