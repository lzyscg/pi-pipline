"""Middleware-owned semantic adapters for the three Lite roles.

The model is responsible for business meaning.  This module accepts several
reasonable surface forms, normalizes them into code-owned objects, and rejects
only missing, ambiguous, or contradictory business semantics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .canonical import CanonicalLyric, InputContractError, canonicalize_lyric, normalize_text


MAX_RESULT_BYTES = 64 * 1024
SUPERVISOR_ACTIONS = {"SEND_GENERATOR", "SEND_REVIEWER", "DELIVER", "ASK_HUMAN"}
REVIEW_SCOPES = {"NONE", "LOCAL", "STRUCTURAL", "INPUT"}
FENCE_LINE = re.compile(r"^\s*```(?:json|text|markdown|md)?\s*$", re.IGNORECASE)


class ContractError(ValueError):
    """The output lacks one unambiguous business result for the next node."""


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


def _normalize(raw: str, name: str) -> str:
    try:
        text = normalize_text(
            raw,
            field_name=name,
            max_bytes=MAX_RESULT_BYTES,
            allow_newlines=True,
        ).strip()
    except InputContractError as exc:
        raise ContractError(str(exc)) from exc
    if not text:
        raise ContractError(f"{name} 为空")
    return text


def _without_wrappers(text: str) -> str:
    lines = text.splitlines()
    cleaned = [
        line
        for line in lines
        if not FENCE_LINE.fullmatch(line)
        and not re.fullmatch(
            r"\s*#\s*(?:SupervisorResult|GenerationResult|ReviewResult)\s+v\d+\s*",
            line,
            re.IGNORECASE,
        )
    ]
    return "\n".join(cleaned).strip()


def _control_surface(line: str) -> str:
    """Remove harmless Markdown decoration from a possible control statement."""
    surface = line.strip()
    surface = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", surface)
    surface = re.sub(r"^#{1,6}\s*", "", surface)
    return surface.replace("**", "").replace("__", "").replace("`", "").strip()


def _json_object(text: str) -> dict | None:
    candidate = _without_wrappers(text)
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def parse_supervisor_result(raw: str) -> SupervisorResult:
    text = _normalize(raw, "SupervisorResult")
    payload = _json_object(text)
    if payload is not None:
        raw_action = payload.get("action", payload.get("动作", payload.get("下一步")))
        if not isinstance(raw_action, str):
            raise ContractError("总控没有给出下一跳动作")
        action = raw_action.strip().upper()
        if action not in SUPERVISOR_ACTIONS:
            raise ContractError("总控下一跳动作无效")
        raw_message = payload.get("message", payload.get("消息", payload.get("说明", "")))
        message = str(raw_message).strip() or f"总控选择 {action}"
        return SupervisorResult(action=action, message=message)

    cleaned = _without_wrappers(text)
    declared: list[tuple[int, str]] = []
    action_line = re.compile(
        r"^\s*(?:(?:ACTION|动作|下一步|路由)\s*[:：]\s*)?"
        r"(SEND_GENERATOR|SEND_REVIEWER|DELIVER|ASK_HUMAN)\s*$",
        re.IGNORECASE,
    )
    for index, line in enumerate(cleaned.splitlines()):
        match = action_line.fullmatch(_control_surface(line))
        if match:
            declared.append((index, match.group(1).upper()))
    actions = {action for _, action in declared}
    if not actions:
        raise ContractError("总控没有明确给出允许识别的下一跳动作")
    if len(actions) != 1:
        raise ContractError("总控给出了互相冲突的下一跳动作")
    action = next(iter(actions))

    lines = cleaned.splitlines()
    message_lines = [
        line
        for index, line in enumerate(lines)
        if index not in {declared_index for declared_index, _ in declared}
        and not re.fullmatch(
            r"\s*(?:MESSAGE|消息|说明)\s*[:：]\s*",
            _control_surface(line),
            re.IGNORECASE,
        )
    ]
    message = "\n".join(message_lines).strip() or f"总控选择 {action}"
    return SupervisorResult(action=action, message=message)


def _canonical_candidate(lines: list[str]) -> CanonicalLyric | None:
    if len(lines) != 16 or any(not line.strip() for line in lines):
        return None
    grouped = "\n\n".join(
        "\n".join(line.strip() for line in lines[index : index + 4])
        for index in range(0, 16, 4)
    )
    try:
        return canonicalize_lyric(grouped)
    except InputContractError:
        return None


def _lyric_candidates(text: str) -> list[CanonicalLyric]:
    candidates: dict[str, CanonicalLyric] = {}

    def add(candidate: CanonicalLyric | None) -> None:
        if candidate is not None:
            candidates[candidate.text] = candidate

    for fenced in re.findall(
        r"```(?:text|markdown|md)?\s*\n(.*?)\n```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        for candidate in _lyric_candidates(fenced):
            add(candidate)

    cleaned = _without_wrappers(text)
    try:
        add(canonicalize_lyric(cleaned))
    except InputContractError:
        pass

    paragraphs = [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", cleaned)
    ]
    for index in range(max(0, len(paragraphs) - 3)):
        group = paragraphs[index : index + 4]
        if len(group) == 4 and all(len(block) == 4 for block in group):
            add(_canonical_candidate([line for block in group for line in block]))

    nonempty = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(nonempty) == 16:
        add(_canonical_candidate(nonempty))
    return list(candidates.values())


def parse_generation_result(raw: str) -> GenerationResult:
    text = _normalize(raw, "GenerationResult")
    payload = _json_object(text)
    summary = "完成歌词"
    search_texts: list[str] = []
    if payload is not None:
        lyric_value = payload.get("lyric", payload.get("歌词", payload.get("content")))
        if not isinstance(lyric_value, str):
            raise ContractError("生成结果没有完整歌词")
        search_texts.append(lyric_value)
        raw_summary = payload.get("summary", payload.get("摘要"))
        if isinstance(raw_summary, str) and raw_summary.strip():
            summary = raw_summary.strip()
    else:
        cleaned = _without_wrappers(text)
        lyric_match = re.search(r"(?im)^\s*(?:LYRIC|歌词)\s*[:：]\s*(.*)$", cleaned)
        if lyric_match:
            inline = lyric_match.group(1).strip()
            body = cleaned[lyric_match.end() :].lstrip("\n")
            search_texts.append("\n".join(part for part in (inline, body) if part))
        search_texts.append(text)
        search_texts.append(cleaned)
        summary_match = re.search(
            r"(?im)^\s*(?:SUMMARY|摘要)\s*[:：]\s*(.*?)(?:\n|$)",
            cleaned,
        )
        if summary_match and summary_match.group(1).strip():
            summary = summary_match.group(1).strip()
        elif summary_match:
            following = cleaned[summary_match.end() :].splitlines()
            if following and following[0].strip():
                summary = following[0].strip()

    candidates: dict[str, CanonicalLyric] = {}
    for search_text in search_texts:
        for candidate in _lyric_candidates(search_text):
            candidates[candidate.text] = candidate
    if not candidates:
        raise ContractError("生成结果中没有可确定的完整 4x4 歌词")
    if len(candidates) != 1:
        raise ContractError("生成结果中存在多份不同歌词，无法确定最新版本")
    return GenerationResult(summary=summary, lyric=next(iter(candidates.values())))


def _review_decision(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    if upper in {"APPROVE", "PASS", "PASSED"} or upper in {"通过", "审核通过", "可交付"}:
        return "APPROVE"
    if upper in {"REPAIR", "REVISE"} or upper in {"返修", "需返修", "不通过", "打回"}:
        return "REPAIR"
    return None


def _review_scope(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    aliases = {
        "NONE": "NONE",
        "无": "NONE",
        "LOCAL": "LOCAL",
        "局部": "LOCAL",
        "定点": "LOCAL",
        "STRUCTURAL": "STRUCTURAL",
        "结构": "STRUCTURAL",
        "结构性": "STRUCTURAL",
        "INPUT": "INPUT",
        "输入": "INPUT",
        "素材": "INPUT",
    }
    return aliases.get(upper)


def _affected_lines(value: object) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    elif isinstance(value, int):
        raw_items = [value]
    elif isinstance(value, str):
        if value.strip().upper() in {"NONE", "无", "无问题行"}:
            return ()
        raw_items = [int(item) for item in re.findall(r"(?<!\d)(?:1[0-6]|[1-9])(?!\d)", value)]
    else:
        return None
    if not raw_items:
        return None
    try:
        affected = tuple(int(item) for item in raw_items)
    except (TypeError, ValueError):
        return None
    if any(item < 1 or item > 16 for item in affected):
        return None
    return tuple(sorted(set(affected)))


def _validate_review(
    decision: str | None,
    affected: tuple[int, ...] | None,
    scope: str | None,
    evidence: str,
) -> ReviewResult:
    if decision not in {"APPROVE", "REPAIR"}:
        raise ContractError("审核结果没有明确且唯一的通过或返修结论")
    if decision == "APPROVE":
        if affected not in {None, ()} or scope not in {None, "NONE"}:
            raise ContractError("审核通过结论与问题行或返修范围互相矛盾")
        return ReviewResult(
            decision="APPROVE",
            affected_lines=(),
            scope="NONE",
            evidence=evidence.strip() or "审核结论：通过",
        )
    if not affected:
        raise ContractError("返修结论缺少 1 至 16 行内的问题行")
    if scope not in REVIEW_SCOPES - {"NONE"}:
        raise ContractError("返修结论缺少 LOCAL、STRUCTURAL 或 INPUT 范围")
    if not evidence.strip():
        raise ContractError("返修结论缺少问题证据")
    return ReviewResult(
        decision="REPAIR",
        affected_lines=affected,
        scope=scope,
        evidence=evidence.strip(),
    )


def parse_review_result(raw: str) -> ReviewResult:
    text = _normalize(raw, "ReviewResult")
    payload = _json_object(text)
    if payload is not None:
        decision = _review_decision(
            payload.get("decision", payload.get("结论", payload.get("审核结论")))
        )
        affected = _affected_lines(
            payload.get(
                "affected_lines",
                payload.get("affectedLines", payload.get("问题行", payload.get("受影响行"))),
            )
        )
        scope = _review_scope(payload.get("scope", payload.get("范围", payload.get("返修范围"))))
        evidence = payload.get("evidence", payload.get("证据", payload.get("理由", "")))
        return _validate_review(
            decision,
            affected,
            scope,
            "" if evidence is None else str(evidence),
        )

    cleaned = _without_wrappers(text)
    lines = cleaned.splitlines()
    control_indexes: set[int] = set()
    decision_values: list[str] = []
    affected: tuple[int, ...] | None = None
    scope: str | None = None
    evidence_parts: list[str] = []
    evidence_index: int | None = None

    for index, line in enumerate(lines):
        surface = _control_surface(line)
        match = re.fullmatch(
            r"\s*(?:DECISION|结论|审核结论|判定)\s*[:：]\s*(.*?)\s*",
            surface,
            re.IGNORECASE,
        )
        natural = re.fullmatch(
            r"\s*(APPROVE|PASS|REPAIR|通过|审核通过|可交付|返修|需返修|不通过|打回)"
            r"(?:\s+(.+?))?\s*",
            surface,
            re.IGNORECASE,
        )
        if match or natural:
            value = (match or natural).group(1)
            decision = _review_decision(value)
            if decision:
                decision_values.append(decision)
                control_indexes.add(index)
                suffix = natural.group(2) if natural else ""
                if suffix:
                    affected = _affected_lines(suffix)
                    scope = _review_scope(next(
                        (token for token in re.split(r"[\s,，、]+", suffix) if _review_scope(token)),
                        "",
                    ))
            continue

        match = re.fullmatch(
            r"\s*(?:AFFECTED_LINES|问题行号?|受影响行)\s*[:：]\s*(.*?)\s*",
            surface,
            re.IGNORECASE,
        )
        if match:
            affected = _affected_lines(match.group(1))
            control_indexes.add(index)
            continue
        match = re.fullmatch(
            r"\s*(?:SCOPE|范围|返修范围)\s*[:：]\s*(.*?)\s*",
            surface,
            re.IGNORECASE,
        )
        if match:
            scope = _review_scope(match.group(1))
            control_indexes.add(index)
            continue
        match = re.fullmatch(
            r"\s*(?:EVIDENCE|证据|理由)\s*[:：]\s*(.*?)\s*",
            surface,
            re.IGNORECASE,
        )
        if match:
            evidence_index = index
            control_indexes.add(index)
            if match.group(1):
                evidence_parts.append(match.group(1).strip())

    natural_surface = _control_surface(cleaned)
    if not decision_values:
        match = re.match(
            r"^\s*(APPROVE|PASS|REPAIR|通过|审核通过|可交付|返修|需返修|不通过|打回)"
            r"(?=$|[\s，,。；;：:])",
            natural_surface,
            re.IGNORECASE,
        )
        natural_decision = _review_decision(match.group(1)) if match else None
        if natural_decision:
            decision_values.append(natural_decision)
    if affected is None:
        match = re.search(
            r"(?:AFFECTED_LINES|问题行号?|受影响行)\s*[:：]\s*([^。；;\n]+)",
            natural_surface,
            re.IGNORECASE,
        )
        if match:
            affected = _affected_lines(match.group(1))
    if scope is None:
        match = re.search(
            r"(?:SCOPE|返修范围|范围)\s*[:：]\s*"
            r"(NONE|LOCAL|STRUCTURAL|INPUT|无|局部|定点|结构性?|输入素材?|素材)",
            natural_surface,
            re.IGNORECASE,
        )
        if match:
            scope = _review_scope(match.group(1))
    if evidence_index is None:
        match = re.search(
            r"(?:EVIDENCE|证据|理由)\s*[:：]\s*(.+)$",
            cleaned,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            evidence_parts = [match.group(1).strip()]
            evidence_index = len(lines)

    decisions = set(decision_values)
    if len(decisions) > 1:
        raise ContractError("审核结果同时给出了通过和返修，语义矛盾")
    decision = next(iter(decisions)) if decisions else None

    if evidence_index is not None:
        evidence_parts.extend(line.strip() for line in lines[evidence_index + 1 :] if line.strip())
    else:
        evidence_parts.extend(
            line.strip()
            for index, line in enumerate(lines)
            if index not in control_indexes and line.strip()
        )
    return _validate_review(decision, affected, scope, "\n".join(evidence_parts))
