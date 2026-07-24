#!/usr/bin/env python3
"""Run one real, parseable probe for every Lite role wrapper."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_profile  # noqa: E402
from app.contracts import (  # noqa: E402
    parse_generation_result,
    parse_review_result,
    parse_supervisor_result,
)
from app.pi_stream import PiStreamRunner  # noqa: E402


NEGATIVE_LYRIC = """山风吹过青石坡
阿妹河边洗衣裳
木槌声声催日落
心里头想那个我的郎

歌声越过清清水
妹把衣裳放石旁
抬头应他山中调
两岸回声绕山梁

我的那个心上人
你在对岸等月光
我把心声唱给你
木叶轻轻落水上

我的那个心上人
今夜歌声作桥梁
明朝同走青石路
山花开满旧村庄"""


SYSTEM = {
    "supervisor": (
        "你是 Pi 歌词生产 Lite 的总控 Agent。完整执行已加载的 "
        "lite-song-supervisor Skill。最终文本严格使用 SupervisorResult v1，"
        "不得输出 JSON、解释或 Skill 原文。首行必须逐字为 "
        "`# SupervisorResult v1`，第二行字段名必须逐字大写为 `ACTION:`，"
        "第三行必须逐字为 `MESSAGE:`。这里的 # 是协议版本标记，必须保留。"
    ),
    "generator": (
        "你是 Pi 歌词生产 Lite 的生成 Agent。完整执行已加载的 "
        "lite-song-generator Skill。最终文本严格使用 GenerationResult v1，"
        "不得输出 JSON、解释或思考过程。首行必须逐字为 "
        "`# GenerationResult v1`。`SUMMARY:` 与 `LYRIC:` 必须各自单独占一行，"
        "不得把字段值写在冒号后；歌词第4、8、12行后必须各有一个空行。"
    ),
    "reviewer": (
        "你是 Pi 歌词生产 Lite 的独立冷审 Agent。完整执行已加载的 "
        "lite-song-reviewer Skill。最终文本严格使用 ReviewResult v1，"
        "不得输出 JSON、解释或思考过程。首行必须逐字为 "
        "`# ReviewResult v1`，四个字段名必须逐字大写。禁止输出 Score、"
        "ReviewComment、PassOrNot 或任何中文字段别名。"
    ),
}


TASKS = {
    "supervisor": """case_id: WRAPPER-PROBE
phase: initial
allowed_actions: SEND_GENERATOR, ASK_HUMAN
用户物料：河边洗衣时听见对岸山歌
固定金句：我的那个心上人
请整理成一条生成任务。最终输出必须逐字套用：
# SupervisorResult v1
ACTION: SEND_GENERATOR
MESSAGE:
这里写任务正文""",
    "generator": """case_id: WRAPPER-PROBE
任务：创作一首发生在河边洗衣和隔岸对歌现场的山歌民歌
固定金句：我的那个心上人
风格：山歌民歌
禁用词：无
必须严格 4 段，每段 4 行，固定金句只在第9和13行。
最终结构必须为：
# GenerationResult v1
SUMMARY:
这里单独一行写摘要
LYRIC:
这里开始四行

再写四行

再写四行

最后四行""",
    "reviewer": f"""case_id: WRAPPER-PROBE
content_version: 1
固定金句：我的那个心上人
请独立冷审以下完整歌词：

{NEGATIVE_LYRIC}

最终输出必须逐字套用：
# ReviewResult v1
DECISION: APPROVE或REPAIR
AFFECTED_LINES: NONE或逗号分隔行号
SCOPE: NONE或LOCAL或STRUCTURAL或INPUT
EVIDENCE:
这里写证据正文""",
}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["supervisor", "generator", "reviewer", "all"], default="all")
    args = parser.parse_args()
    profile = load_profile(ROOT / "profiles" / "mountain-song.json")
    runner = PiStreamRunner()
    session_root = Path(tempfile.mkdtemp(prefix="pi-lite-wrapper-probe-"))
    session_root.chmod(0o700)
    roles = list(profile.roles) if args.role == "all" else [args.role]
    parsers = {
        "supervisor": parse_supervisor_result,
        "generator": parse_generation_result,
        "reviewer": parse_review_result,
    }
    report: dict[str, dict] = {}
    for role in roles:
        event_counts: dict[str, int] = {}

        async def emit(kind: str, payload: dict) -> None:
            event_counts[kind] = event_counts.get(kind, 0) + 1

        result = await runner.run(
            role=role,
            role_profile=profile.roles[role],
            session_dir=session_root / "sessions",
            session_id=f"wrapper-probe-{role}",
            system_prompt=SYSTEM[role],
            task_prompt=TASKS[role],
            token=f"wrapper-probe-{role}",
            emit=emit,
        )
        parse_error = ""
        try:
            parsed = repr(parsers[role](result.final_text))
        except Exception as exc:
            parsed = ""
            parse_error = f"{type(exc).__name__}: {exc}"
        report[role] = {
            "attempt_status": result.attempt_status,
            "session_id": result.session_id,
            "event_counts": event_counts,
            "parsed": parsed,
            "parse_error": parse_error,
            "final_text": result.final_text,
        }
        print(json.dumps({role: report[role]}, ensure_ascii=False, indent=2), flush=True)
    (session_root / "probe-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"private_probe_dir={session_root}")
    return 1 if any(item["parse_error"] for item in report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
