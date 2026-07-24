"""Best-effort secret redaction for diagnostic prompts and Pi event text."""

from __future__ import annotations

import re


PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|secret)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def redact_text(value: str) -> str:
    redacted = value
    for pattern in PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", redacted)
    return redacted

