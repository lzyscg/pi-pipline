"""One canonical 4x4 lyric representation shared by gates, diff, and UI."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


class InputContractError(ValueError):
    """User or model text violates the Lite input contract."""


@dataclass(frozen=True, slots=True)
class CanonicalLyric:
    raw: str
    text: str
    stanzas: tuple[tuple[str, ...], ...]
    lines: tuple[str, ...]


def normalize_text(
    value: str,
    *,
    field_name: str,
    max_bytes: int,
    allow_newlines: bool = True,
) -> str:
    if not isinstance(value, str):
        raise InputContractError(f"{field_name} 必须是字符串")
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    for char in value:
        if unicodedata.category(char) == "Cc" and char not in {"\n", "\t"}:
            raise InputContractError(f"{field_name} 包含非法控制字符")
    if not allow_newlines and ("\n" in value or "\t" in value):
        raise InputContractError(f"{field_name} 必须是单行文本")
    if len(value.encode("utf-8")) > max_bytes:
        raise InputContractError(f"{field_name} 超过 {max_bytes} 字节上限")
    return value


def normalize_golden_line(value: str) -> str:
    value = normalize_text(
        value,
        field_name="固定金句",
        max_bytes=512,
        allow_newlines=False,
    ).strip()
    if not value:
        raise InputContractError("固定金句不能为空")
    return value


def canonicalize_lyric(raw: str) -> CanonicalLyric:
    normalized = normalize_text(
        raw,
        field_name="歌词",
        max_bytes=64 * 1024,
        allow_newlines=True,
    )
    if normalized.startswith("\n") or normalized.endswith("\n\n"):
        raise InputContractError("歌词不得包含前置或尾部空段")
    normalized = normalized.rstrip("\n")
    if "\n\n\n" in normalized:
        raise InputContractError("段落之间必须恰好一个空行")
    blocks = normalized.split("\n\n")
    if len(blocks) != 4:
        raise InputContractError("歌词必须恰好包含四段")
    stanzas: list[tuple[str, ...]] = []
    for stanza_number, block in enumerate(blocks, 1):
        lines = block.split("\n")
        if len(lines) != 4:
            raise InputContractError(f"第 {stanza_number} 段必须恰好四行")
        if any(not line or line != line.strip() for line in lines):
            raise InputContractError(f"第 {stanza_number} 段包含空行或行首尾空白")
        stanzas.append(tuple(lines))
    line_tuple = tuple(line for stanza in stanzas for line in stanza)
    canonical_text = "\n\n".join("\n".join(stanza) for stanza in stanzas)
    return CanonicalLyric(
        raw=raw,
        text=canonical_text,
        stanzas=tuple(stanzas),
        lines=line_tuple,
    )


def changed_line_numbers(before: CanonicalLyric, after: CanonicalLyric) -> list[int]:
    return [
        number
        for number, (old, new) in enumerate(zip(before.lines, after.lines), 1)
        if old != new
    ]


def locked_lines_unchanged(
    before: CanonicalLyric,
    after: CanonicalLyric,
    allowed_lines: list[int] | tuple[int, ...],
) -> bool:
    allowed = set(allowed_lines)
    return all(
        number in allowed or old == new
        for number, (old, new) in enumerate(zip(before.lines, after.lines), 1)
    )

