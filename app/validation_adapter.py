"""Adapt canonical Lite lyrics to the existing deterministic validator."""

from __future__ import annotations

import importlib.util
from functools import lru_cache

from .canonical import CanonicalLyric, locked_lines_unchanged
from .provenance import SUPERVISOR_VALIDATOR


@lru_cache(maxsize=1)
def _validator_module():
    spec = importlib.util.spec_from_file_location("lite_existing_validation", SUPERVISOR_VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Validator：{SUPERVISOR_VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_canonical(
    lyric: CanonicalLyric,
    golden_line: str,
    forbidden_words: str = "",
) -> dict:
    result = _validator_module().validate_lyric(
        lyric.text,
        golden_line,
        forbidden_words,
    )
    occurrence_positions = [
        number for number, line in enumerate(lyric.lines, 1) if golden_line in line
    ]
    occurrence_gate = (
        occurrence_positions == [9, 13]
        and lyric.lines[8] == golden_line
        and lyric.lines[12] == golden_line
    )
    result["checks"]["golden_line_only_at_9_and_13"] = occurrence_gate
    result["golden_line_occurrence_positions"] = occurrence_positions
    result["duplicate_non_golden_occurrences"] = [
        {
            "text": duplicate,
            "positions": [
                number
                for number, line in enumerate(lyric.lines, 1)
                if line == duplicate
            ],
        }
        for duplicate in result.get("duplicate_non_golden_lines", [])
    ]
    result["pass"] = all(result["checks"].values())
    result["canonical_line_count"] = len(lyric.lines)
    result["canonical_stanza_count"] = len(lyric.stanzas)
    return result


def validate_local_repair(
    before: CanonicalLyric,
    after: CanonicalLyric,
    allowed_lines: list[int],
) -> dict:
    changed = [
        number
        for number, (old, new) in enumerate(zip(before.lines, after.lines), 1)
        if old != new
    ]
    return {
        "allowed_lines": list(allowed_lines),
        "locked_lines": [line for line in range(1, 17) if line not in allowed_lines],
        "changed_lines": changed,
        "locked_lines_unchanged": locked_lines_unchanged(before, after, allowed_lines),
        "changed_only_allowed": set(changed).issubset(set(allowed_lines)),
    }
