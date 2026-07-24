"""Deterministic lyric release gates owned by Pi Swimlane Lite.

This is a local copy of the previously reused Supervisor Runtime validator.
Keeping it here makes Lite independently runnable without changing or loading
the existing Supervisor Runtime.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


PUNCTUATION = re.compile(r"[，。！？；：、,.!?;:（）()《》【】\"“”‘’…—]")


def lyric_lines(lyrics: str) -> list[str]:
    return [line.strip() for line in lyrics.splitlines() if line.strip()]


def validate_lyric(lyrics: str, golden_line: str, forbidden_words: str = "") -> dict[str, Any]:
    lines = lyric_lines(lyrics)
    stanzas = [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", lyrics.strip())
        if block.strip()
    ]
    gold_positions = [number for number, line in enumerate(lines, 1) if line == golden_line]
    punctuation_lines = [number for number, line in enumerate(lines, 1) if PUNCTUATION.search(line)]
    duplicate_lines = sorted(
        line for line, count in Counter(lines).items() if line != golden_line and count > 1
    )
    forbidden_terms = [
        term for term in re.split(r"[\s,，、;；]+", forbidden_words.strip()) if term
    ]
    forbidden_hits = [
        {"line": number, "term": term}
        for number, line in enumerate(lines, 1)
        for term in forbidden_terms
        if term in line
    ]
    line_lengths = [len(re.sub(r"\s+", "", line)) for line in lines]
    short_line_numbers = [
        number
        for number, (line, length) in enumerate(zip(lines, line_lengths), 1)
        if line != golden_line and length < 7
    ]
    long_line_numbers = [
        number
        for number, (line, length) in enumerate(zip(lines, line_lengths), 1)
        if line != golden_line and length > 11
    ]
    checks = {
        "exactly_16_lines": len(lines) == 16,
        "four_stanzas_of_four": len(stanzas) == 4 and all(len(stanza) == 4 for stanza in stanzas),
        "golden_line_only_at_9_and_13": gold_positions == [9, 13],
        "no_punctuation": not punctuation_lines,
        "no_non_golden_duplicate": not duplicate_lines,
        "no_forbidden_words": not forbidden_hits,
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "line_count": len(lines),
        "gold_positions": gold_positions,
        "punctuation_lines": punctuation_lines,
        "duplicate_non_golden_lines": duplicate_lines,
        "forbidden_word_hits": forbidden_hits,
        "soft_line_length_metrics": {
            "line_lengths": line_lengths,
            "short_line_numbers": short_line_numbers,
            "long_line_numbers": long_line_numbers,
            "is_release_gate": False,
        },
    }
