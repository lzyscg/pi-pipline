"""Streaming Pi CLI adapter for Lite.

Unlike the existing production gateway, this adapter preserves normalized
thinking/text/tool diagnostics and waits for an explicit terminal boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .config import RoleProfile
from .provenance import LITE_ROOT, REPO_ROOT
from .redaction import redact_text


MAX_PI_EVENT_BYTES = 1024 * 1024
EmitCallback = Callable[[str, dict], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class NormalizedPiEvent:
    kind: str
    payload: dict


@dataclass(slots=True)
class PiEventParser:
    final_text: str = ""
    session_id: str = ""
    terminal_seen: bool = False
    error_seen: bool = False

    def feed_line(self, raw_line: bytes | str) -> list[NormalizedPiEvent]:
        raw = raw_line.encode("utf-8") if isinstance(raw_line, str) else raw_line
        if len(raw) > MAX_PI_EVENT_BYTES:
            return [
                NormalizedPiEvent(
                    "unknown_pi_event",
                    {
                        "reason": "event_too_large",
                        "size": len(raw),
                        "truncated": True,
                    },
                )
            ]
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return [
                NormalizedPiEvent(
                    "unknown_pi_event",
                    {"reason": "invalid_json", "size": len(raw), "truncated": False},
                )
            ]
        event_type = str(event.get("type", ""))
        if event_type == "session":
            self.session_id = str(event.get("id", ""))
            return [
                NormalizedPiEvent(
                    "pi_session",
                    {"session_id": self.session_id, "version": event.get("version")},
                )
            ]
        if event_type == "message_update":
            assistant_event = event.get("assistantMessageEvent")
            if not isinstance(assistant_event, dict):
                return [self._unknown(event_type, event)]
            update_type = str(assistant_event.get("type", ""))
            if update_type in {"thinking_start", "thinking_end", "text_start"}:
                return [
                    NormalizedPiEvent(
                        update_type,
                        {"content_index": assistant_event.get("contentIndex")},
                    )
                ]
            if update_type == "thinking_delta":
                return [
                    NormalizedPiEvent(
                        "thinking_delta",
                        {"text": redact_text(str(assistant_event.get("delta", "")))},
                    )
                ]
            if update_type == "text_delta":
                return [
                    NormalizedPiEvent(
                        "text_delta",
                        {"text": redact_text(str(assistant_event.get("delta", "")))},
                    )
                ]
            if update_type == "text_end":
                content = str(assistant_event.get("content", ""))
                if content:
                    self.final_text = content
                return [
                    NormalizedPiEvent(
                        "text_end",
                        {"text": redact_text(content), "candidate": True},
                    )
                ]
            if update_type.startswith("tool_"):
                return [
                    NormalizedPiEvent(
                        "tool_event",
                        {
                            "tool": str(assistant_event.get("toolName", "")),
                            "status": update_type,
                        },
                    )
                ]
            return [self._unknown(f"message_update:{update_type}", assistant_event)]

        if event_type in {"message_end", "turn_end"}:
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                text = _message_text(message)
                if text:
                    self.final_text = text
                if message.get("stopReason") == "stop":
                    self.terminal_seen = True
                    return [
                        NormalizedPiEvent(
                            "pi_terminal",
                            {"boundary": event_type, "stop_reason": "stop"},
                        )
                    ]
            return [NormalizedPiEvent("pi_lifecycle", {"type": event_type})]

        if event_type == "agent_end":
            self.terminal_seen = True
            messages = event.get("messages")
            if isinstance(messages, list):
                for message in reversed(messages):
                    if isinstance(message, dict) and message.get("role") == "assistant":
                        text = _message_text(message)
                        if text:
                            self.final_text = text
                            break
            return [
                NormalizedPiEvent(
                    "pi_terminal",
                    {"boundary": "agent_end", "will_retry": bool(event.get("willRetry"))},
                )
            ]
        if event_type == "agent_settled":
            self.terminal_seen = True
            return [NormalizedPiEvent("pi_terminal", {"boundary": "agent_settled"})]
        if event_type in {"error", "agent_error", "turn_error"}:
            self.error_seen = True
            return [
                NormalizedPiEvent(
                    "pi_error",
                    {
                        "type": event_type,
                        "summary": redact_text(str(event.get("message") or event.get("error") or "Pi error"))[
                            :1000
                        ],
                    },
                )
            ]
        if event_type in {
            "agent_start",
            "turn_start",
            "message_start",
            "tool_execution_start",
            "tool_execution_end",
        }:
            payload = {"type": event_type}
            if event_type.startswith("tool_"):
                payload.update(
                    {
                        "tool": str(event.get("toolName", "")),
                        "status": event_type,
                    }
                )
                return [NormalizedPiEvent("tool_event", payload)]
            return [NormalizedPiEvent("pi_lifecycle", payload)]
        return [self._unknown(event_type or "missing_type", event)]

    @staticmethod
    def _unknown(event_type: str, event: dict) -> NormalizedPiEvent:
        return NormalizedPiEvent(
            "unknown_pi_event",
            {
                "source_type": event_type,
                "keys": sorted(str(key) for key in event.keys())[:32],
                "truncated": False,
            },
        )


def _message_text(message: dict) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()


@dataclass(frozen=True, slots=True)
class PiRunResult:
    attempt_status: str
    final_text: str
    session_id: str
    return_code: int | None
    error_summary: str = ""


@dataclass(slots=True)
class _ActiveProcess:
    process: asyncio.subprocess.Process
    token: str
    stopped: bool = False


class PiStreamRunner:
    def __init__(self, pi_binary: str = "pi") -> None:
        self.pi_binary = pi_binary
        self._active: dict[str, _ActiveProcess] = {}
        self._active_lock = asyncio.Lock()

    def command(
        self,
        *,
        role: str,
        role_profile: RoleProfile,
        session_dir: Path,
        session_id: str,
        system_prompt: str,
        task_prompt: str,
    ) -> list[str]:
        skill_path = LITE_ROOT / "skills" / role_profile.skill
        return [
            self.pi_binary,
            "--model",
            role_profile.model,
            "--thinking",
            role_profile.thinking,
            "--mode",
            "json",
            "--print",
            "--session-dir",
            str(session_dir),
            "--session-id",
            session_id,
            "--name",
            f"lite-song-{role}",
            "--skill",
            str(skill_path),
            "--no-context-files",
            "--no-tools",
            "--approve",
            "--system-prompt",
            system_prompt,
            task_prompt,
        ]

    async def run(
        self,
        *,
        role: str,
        role_profile: RoleProfile,
        session_dir: Path,
        session_id: str,
        system_prompt: str,
        task_prompt: str,
        token: str,
        emit: EmitCallback,
        timeout_seconds: float = 480,
    ) -> PiRunResult:
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = self.command(
            role=role,
            role_profile=role_profile,
            session_dir=session_dir,
            session_id=session_id,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_PI_EVENT_BYTES * 2,
            start_new_session=True,
        )
        active = _ActiveProcess(process=process, token=token)
        async with self._active_lock:
            self._active[token] = active
        parser = PiEventParser()
        stderr_task = asyncio.create_task(process.stderr.read() if process.stderr else _empty_bytes())
        try:
            await asyncio.wait_for(
                self._consume_stdout(process, parser, emit),
                timeout=timeout_seconds,
            )
            return_code = await process.wait()
        except asyncio.TimeoutError:
            await self._terminate_group(active)
            stderr = await stderr_task
            return PiRunResult(
                attempt_status="ambiguous",
                final_text=parser.final_text,
                session_id=parser.session_id or session_id,
                return_code=process.returncode,
                error_summary=redact_text(stderr.decode("utf-8", errors="replace"))[:1000]
                or "Pi timeout",
            )
        except asyncio.CancelledError:
            await self._terminate_group(active)
            raise
        finally:
            async with self._active_lock:
                self._active.pop(token, None)

        stderr = await stderr_task
        summary = redact_text(stderr.decode("utf-8", errors="replace").strip())[:1000]
        if active.stopped:
            status = "killed"
        elif parser.final_text and parser.terminal_seen and return_code == 0:
            status = "succeeded"
        elif parser.error_seen and not parser.final_text:
            status = "known_failed"
        else:
            status = "ambiguous"
        return PiRunResult(
            attempt_status=status,
            final_text=parser.final_text,
            session_id=parser.session_id or session_id,
            return_code=return_code,
            error_summary=summary,
        )

    async def _consume_stdout(
        self,
        process: asyncio.subprocess.Process,
        parser: PiEventParser,
        emit: EmitCallback,
    ) -> None:
        if process.stdout is None:
            raise RuntimeError("Pi stdout 不可用")
        buffered_kind = ""
        buffered_text = ""
        last_flush = time.monotonic()

        async def flush() -> None:
            nonlocal buffered_kind, buffered_text, last_flush
            if buffered_kind and buffered_text:
                await emit(buffered_kind, {"text": buffered_text})
            buffered_kind = ""
            buffered_text = ""
            last_flush = time.monotonic()

        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            for normalized in parser.feed_line(raw):
                if normalized.kind in {"thinking_delta", "text_delta"}:
                    text = str(normalized.payload.get("text", ""))
                    if buffered_kind and buffered_kind != normalized.kind:
                        await flush()
                    buffered_kind = normalized.kind
                    buffered_text += text
                    if time.monotonic() - last_flush >= 0.15:
                        await flush()
                else:
                    await flush()
                    await emit(normalized.kind, normalized.payload)
        await flush()

    async def stop(self, token: str) -> bool:
        async with self._active_lock:
            active = self._active.get(token)
        if active is None or active.process.returncode is not None:
            return False
        active.stopped = True
        await self._terminate_group(active)
        return True

    @staticmethod
    async def _terminate_group(active: _ActiveProcess) -> None:
        process = active.process
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


async def _empty_bytes() -> bytes:
    return b""

