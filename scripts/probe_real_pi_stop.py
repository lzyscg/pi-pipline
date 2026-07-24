#!/usr/bin/env python3
"""Probe real Pi stop/timeout semantics and process-group cleanup."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_profile  # noqa: E402
from app.pi_stream import PiStreamRunner  # noqa: E402
from app.prompts import SYSTEM_PROMPTS  # noqa: E402


async def main() -> int:
    profile = load_profile(ROOT / "profiles" / "mountain-song.json")
    runner = PiStreamRunner()
    session_dir = ROOT / "runtime" / "stop-probe-sessions"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    token = f"real-stop-{stamp}"
    first_event = asyncio.Event()
    kinds: list[str] = []

    async def emit(kind: str, payload: dict) -> None:
        kinds.append(kind)
        first_event.set()

    stop_task = asyncio.create_task(
        runner.run(
            role="generator",
            role_profile=profile.roles["generator"],
            session_dir=session_dir,
            session_id=f"stop-probe-{stamp}",
            system_prompt=SYSTEM_PROMPTS["generator"],
            task_prompt="先详细思考如何写一首山歌，再按合同输出完整歌词",
            token=token,
            emit=emit,
        )
    )
    await asyncio.wait_for(first_event.wait(), timeout=60)
    stopped = await runner.stop(token)
    stop_result = await stop_task

    async def timeout_emit(kind: str, payload: dict) -> None:
        return None

    timeout_result = await runner.run(
        role="generator",
        role_profile=profile.roles["generator"],
        session_dir=session_dir,
        session_id=f"timeout-probe-{stamp}",
        system_prompt=SYSTEM_PROMPTS["generator"],
        task_prompt="按合同输出完整歌词",
        token=f"real-timeout-{stamp}",
        emit=timeout_emit,
        timeout_seconds=0.01,
    )
    report = {
        "real_pi": True,
        "stop_requested": stopped,
        "stop_attempt_status": stop_result.attempt_status,
        "stop_return_code": stop_result.return_code,
        "events_before_stop": kinds,
        "timeout_attempt_status": timeout_result.attempt_status,
        "timeout_return_code": timeout_result.return_code,
        "active_process_tokens_after_probe": sorted(runner._active),
        "process_groups_cleaned": not runner._active,
    }
    output = ROOT / "reports" / "real_pi_stop_probe.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if (
        stopped
        and stop_result.attempt_status == "killed"
        and timeout_result.attempt_status == "ambiguous"
        and not runner._active
    ) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
