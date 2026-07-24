"""Code-owned Case, Turn, and Attempt state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CaseStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TurnStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    ORPHANED = "orphaned"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    KNOWN_FAILED = "known_failed"
    AMBIGUOUS = "ambiguous"
    KILLED = "killed"


CASE_TRANSITIONS = {
    CaseStatus.CREATED: {CaseStatus.RUNNING, CaseStatus.CANCELLED, CaseStatus.FAILED},
    CaseStatus.RUNNING: {
        CaseStatus.WAITING_HUMAN,
        CaseStatus.DELIVERED,
        CaseStatus.CANCELLED,
        CaseStatus.FAILED,
    },
    CaseStatus.WAITING_HUMAN: {
        CaseStatus.RUNNING,
        CaseStatus.CANCELLED,
        CaseStatus.FAILED,
    },
    CaseStatus.DELIVERED: set(),
    CaseStatus.CANCELLED: set(),
    CaseStatus.FAILED: set(),
}

TURN_TRANSITIONS = {
    TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.CANCELLED},
    TurnStatus.RUNNING: {
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.INCOMPLETE,
        TurnStatus.ORPHANED,
        TurnStatus.CANCELLED,
    },
    TurnStatus.COMPLETED: set(),
    TurnStatus.FAILED: set(),
    TurnStatus.INCOMPLETE: set(),
    TurnStatus.ORPHANED: set(),
    TurnStatus.CANCELLED: set(),
}


def require_transition(current: Enum, target: Enum, table: dict) -> None:
    if target not in table[current]:
        raise ValueError(f"非法状态转换：{current.value} → {target.value}")


@dataclass(slots=True)
class TurnState:
    turn_id: str
    role: str
    token: str
    status: TurnStatus = TurnStatus.QUEUED
    parent_turn_id: str | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, target: TurnStatus, *, token: str | None = None) -> None:
        if token is not None and token != self.token:
            raise ValueError("Turn token 不匹配")
        require_transition(self.status, target, TURN_TRANSITIONS)
        self.status = target


def allowed_actions(
    *,
    phase: str,
    hard_pass: bool | None = None,
    review_decision: str | None = None,
    review_scope: str | None = None,
) -> tuple[str, ...]:
    if phase == "initial":
        return ("SEND_GENERATOR", "ASK_HUMAN")
    if phase == "generated" and hard_pass:
        return ("SEND_REVIEWER", "ASK_HUMAN")
    if phase == "generated" and hard_pass is False:
        return ("SEND_GENERATOR", "ASK_HUMAN")
    if phase == "reviewed":
        if review_decision == "APPROVE" and review_scope == "NONE" and hard_pass:
            return ("DELIVER", "ASK_HUMAN")
        if review_decision == "REPAIR" and review_scope == "LOCAL":
            return ("SEND_GENERATOR", "ASK_HUMAN")
        if review_decision == "REPAIR" and review_scope in {"STRUCTURAL", "INPUT"}:
            return ("ASK_HUMAN",)
    return ("ASK_HUMAN",)

