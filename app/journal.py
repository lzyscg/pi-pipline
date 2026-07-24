"""Append-only case journal: the sole source of business truth."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class JournalEvent:
    schema_version: str
    case_id: str
    turn_id: str | None
    attempt_id: str | None
    event_id: int
    message_id: str | None
    parent_id: str | None
    content_version: int | None
    status: str | None
    event_type: str
    created_at: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "turn_id": self.turn_id,
            "attempt_id": self.attempt_id,
            "event_id": self.event_id,
            "message_id": self.message_id,
            "parent_id": self.parent_id,
            "content_version": self.content_version,
            "status": self.status,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "payload": self.payload,
        }


class CaseJournal:
    def __init__(self, case_dir: Path, case_id: str) -> None:
        self.case_dir = case_dir
        self.case_id = case_id
        self.path = case_dir / "journal.jsonl"
        self.snapshot_path = case_dir / "case.snapshot.json"
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self.events = self._load_and_recover()
        self._next_id = (self.events[-1]["event_id"] + 1) if self.events else 1

    def _load_and_recover(self) -> list[dict]:
        self.case_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.case_dir.chmod(0o700)
        if not self.path.exists():
            self.path.touch(mode=0o600)
            return []
        raw = self.path.read_bytes()
        if not raw:
            return []
        lines = raw.splitlines(keepends=True)
        events: list[dict] = []
        recovered = False
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                if index != len(lines) - 1:
                    raise RuntimeError("journal 中间记录损坏，拒绝恢复") from exc
                recovered = True
                break
            expected = len(events) + 1
            if event.get("event_id") != expected or event.get("case_id") != self.case_id:
                raise RuntimeError("journal event_id 或 case_id 不连续")
            events.append(event)
        if recovered:
            valid = b"".join(lines[: len(events)])
            self.path.write_bytes(valid)
            with self.path.open("ab", buffering=0) as handle:
                recovery = {
                    "schema_version": "journal_event_v1",
                    "case_id": self.case_id,
                    "turn_id": None,
                    "attempt_id": None,
                    "event_id": len(events) + 1,
                    "message_id": None,
                    "parent_id": None,
                    "content_version": None,
                    "status": "recovered",
                    "event_type": "storage_recovered",
                    "created_at": utc_now(),
                    "payload": {"detail": "truncated incomplete final journal record"},
                }
                encoded = (json.dumps(recovery, ensure_ascii=False) + "\n").encode("utf-8")
                handle.write(encoded)
                os.fsync(handle.fileno())
                events.append(recovery)
        return events

    async def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
        attempt_id: str | None = None,
        message_id: str | None = None,
        parent_id: str | None = None,
        content_version: int | None = None,
        status: str | None = None,
        durable: bool = False,
    ) -> dict:
        async with self._lock:
            event = JournalEvent(
                schema_version="journal_event_v1",
                case_id=self.case_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                event_id=self._next_id,
                message_id=message_id,
                parent_id=parent_id,
                content_version=content_version,
                status=status,
                event_type=event_type,
                created_at=utc_now(),
                payload=payload,
            ).to_dict()
            encoded = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            with self.path.open("ab", buffering=0) as handle:
                handle.write(encoded)
                if durable:
                    os.fsync(handle.fileno())
            self.events.append(event)
            self._next_id += 1
            for queue in tuple(self._subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self._subscribers.discard(queue)
            return event

    def replay_after(self, event_id: int) -> list[dict]:
        return [event for event in self.events if event["event_id"] > event_id]

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(queue)

    def write_snapshot(self, state: dict) -> None:
        cached = dict(state)
        cached["last_event_id"] = self.events[-1]["event_id"] if self.events else 0
        temp = self.snapshot_path.with_suffix(".tmp")
        temp.write_text(json.dumps(cached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.snapshot_path)

