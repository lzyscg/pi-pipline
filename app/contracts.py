"""Strict minimal text contracts for the three Lite roles."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .canonical import CanonicalLyric, InputContractError, canonicalize_lyric, normalize_text


MAX_RESULT_BYTES = 64 * 1024
RESERVED_FIELD = re.compile(
    r"^(?:ACTION|MESSAGE|SUMMARY|LYRIC|DECISION|AFFECTED_LINES|SCOPE|EVIDENCE):",
    re.MULTILINE,
)


class ContractError(ValueError):
    """A final assistant result cannot safely enter the next node."""


@dataclass(frozen=True, slots=True)
class SupervisorResult:
    action: str
    message: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    summary: str
    lyric: CanonicalLyric


@dataclass(frozen=True, slots=True)
class ReviewResult:
    decision: str
    affected_lines: tuple[int, ...]
    scope: str
    evidence: str


def _prepare(raw: str, version: str) -> list[str]:
    try:
        normalized = normalize_text(
            raw,
            field_name=version,
            max_bytes=MAX_RESULT_BYTES,
            allow_newlines=True,
        )
    except InputContractError as exc:
        raise ContractError(str(exc)) from exc
    normalized = normalized.rstrip("\n")
    if not normalized:
        raise ContractError(f"{version} 为空")
    lines = normalized.split("\n")
    if lines[0] == f"# {version}":
        lines = lines[1:]
    elif lines[0].startswith("#"):
        raise ContractError(f"{version} 版本行无效")
    if not lines or not lines[0]:
        raise ContractError(f"{version} 不允许前置空行")
    if any("```" in line for line in lines):
        raise ContractError(f"{version} 不允许代码围栏")
    if any(line.startswith("#") for line in lines):
        raise ContractError(f"{version} 不允许 Markdown 标题")
    return lines


def _body(lines: list[str], start: int, *, name: str) -> str:
    body = "\n".join(lines[start:]).strip()
    if not body:
        raise ContractError(f"{name} 正文不能为空")
    if RESERVED_FIELD.search(body):
        raise ContractError(f"{name} 正文中出现控制字段")
    return body


def parse_supervisor_result(raw: str) -> SupervisorResult:
    lines = _prepare(raw, "SupervisorResult v1")
    if len(lines) < 3 or not lines[0].startswith("ACTION: ") or lines[1] != "MESSAGE:":
        raise ContractError("SupervisorResult 字段名称或顺序无效")
    action = lines[0][len("ACTION: ") :]
    if action not in {"SEND_GENERATOR", "SEND_REVIEWER", "DELIVER", "ASK_HUMAN"}:
        raise ContractError("SupervisorResult ACTION 无效")
    message = _body(lines, 2, name="MESSAGE")
    return SupervisorResult(action=action, message=message)


def parse_generation_result(raw: str) -> GenerationResult:
    lines = _prepare(raw, "GenerationResult v1")
    if not lines or lines[0] != "SUMMARY:":
        raise ContractError("GenerationResult 必须从 SUMMARY 字段开始")
    lyric_headers = [index for index, line in enumerate(lines) if line == "LYRIC:"]
    if lyric_headers != [2]:
        raise ContractError("GenerationResult 必须包含单行 SUMMARY 和唯一 LYRIC 字段")
    summary = lines[1].strip()
    if not summary or RESERVED_FIELD.search(summary):
        raise ContractError("GenerationResult SUMMARY 无效")
    lyric_raw = "\n".join(lines[3:])
    try:
        lyric = canonicalize_lyric(lyric_raw)
    except InputContractError as exc:
        raise ContractError(str(exc)) from exc
    return GenerationResult(summary=summary, lyric=lyric)


def parse_review_result(raw: str) -> ReviewResult:
    lines = _prepare(raw, "ReviewResult v1")
    if len(lines) < 5:
        raise ContractError("ReviewResult 字段不完整")
    prefixes = ("DECISION: ", "AFFECTED_LINES: ", "SCOPE: ")
    if any(not lines[index].startswith(prefix) for index, prefix in enumerate(prefixes)):
        raise ContractError("ReviewResult 字段名称或顺序无效")
    if lines[3] != "EVIDENCE:":
        raise ContractError("ReviewResult 缺少 EVIDENCE 字段")
    decision = lines[0][len(prefixes[0]) :]
    affected_raw = lines[1][len(prefixes[1]) :]
    scope = lines[2][len(prefixes[2]) :]
    if decision not in {"APPROVE", "REPAIR"}:
        raise ContractError("ReviewResult DECISION 无效")
    if scope not in {"NONE", "LOCAL", "STRUCTURAL", "INPUT"}:
        raise ContractError("ReviewResult SCOPE 无效")
    if affected_raw == "NONE":
        affected: tuple[int, ...] = ()
    else:
        if not re.fullmatch(r"(?:[1-9]|1[0-6])(?:,(?:[1-9]|1[0-6]))*", affected_raw):
            raise ContractError("ReviewResult AFFECTED_LINES 无效")
        affected = tuple(int(item) for item in affected_raw.split(","))
        if tuple(sorted(set(affected))) != affected:
            raise ContractError("AFFECTED_LINES 必须升序且不重复")
    if decision == "APPROVE" and (affected or scope != "NONE"):
        raise ContractError("APPROVE 只能与 NONE/NONE 组合")
    if decision == "REPAIR" and not affected:
        raise ContractError("REPAIR 必须给出受影响行")
    if decision == "REPAIR" and scope == "NONE":
        raise ContractError("REPAIR 不能使用 NONE scope")
    evidence = _body(lines, 4, name="EVIDENCE")
    return ReviewResult(
        decision=decision,
        affected_lines=affected,
        scope=scope,
        evidence=evidence,
    )

