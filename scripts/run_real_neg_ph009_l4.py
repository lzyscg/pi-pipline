#!/usr/bin/env python3
"""Real Pi NEG-PH009-L4 detection, repair, and fresh cold review."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.canonical import canonicalize_lyric  # noqa: E402
from app.config import load_profile  # noqa: E402
from app.contracts import ContractError, parse_generation_result, parse_review_result  # noqa: E402
from app.pi_stream import PiStreamRunner  # noqa: E402
from app.prompts import SYSTEM_PROMPTS, generator_prompt, reviewer_prompt  # noqa: E402
from app.validation_adapter import validate_canonical, validate_local_repair  # noqa: E402

GOLDEN = "我的那个心上人"
V1 = """山风吹过青石坡
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


async def invoke(runner, profile, role, session_id, prompt):
    counts = {}
    async def emit(kind, payload):
        counts[kind] = counts.get(kind, 0) + 1
    result = await runner.run(
        role=role,
        role_profile=profile.roles[role],
        session_dir=ROOT / "runtime" / "neg-probe-sessions",
        session_id=session_id,
        system_prompt=SYSTEM_PROMPTS[role],
        task_prompt=prompt,
        token=f"{session_id}-token",
        emit=emit,
    )
    return result, counts


async def main():
    profile = load_profile(ROOT / "profiles" / "mountain-song.json")
    runner = PiStreamRunner()
    before = canonicalize_lyric(V1)
    review1_prompt = reviewer_prompt(
        {"case_id": "NEG-PH009-L4", "content_version": 1, "golden_line": GOLDEN},
        V1,
    )
    review1_raw, review1_events = await invoke(
        runner, profile, "reviewer", "NEG-PH009-L4__reviewer__v1", review1_prompt
    )
    review1 = parse_review_result(review1_raw.final_text)
    detection_hit = (
        review1.decision == "REPAIR"
        and review1.affected_lines == (4,)
        and review1.scope == "LOCAL"
    )

    envelope = {
        "case_id": "NEG-PH009-L4",
        "content_version": 1,
        "golden_line": GOLDEN,
        "style": "山歌民歌",
        "forbidden_words": "",
        "allowed_lines": [4],
        "locked_lines": [line for line in range(1, 17) if line != 4],
        "previous_lyric": V1,
        "fixture_scope": True,
    }
    repair_prompt = generator_prompt(
        envelope,
        "固定负控机制返修：第4行词序不自然。只修改第4行，使其成为自然口语；其他15行逐字冻结。",
    )
    generation_raw, generation_events = await invoke(
        runner, profile, "generator", "NEG-PH009-L4__generator", repair_prompt
    )
    generation_contract_failures = []
    try:
        generation = parse_generation_result(generation_raw.final_text)
    except ContractError as exc:
        # The invalid result is evidence only: it never becomes a lyric version
        # and never routes downstream. A new correction Turn reuses the Case's
        # persistent generator Session, matching the runtime's manual recovery
        # rule without weakening the contract parser.
        generation_contract_failures.append(
            {"error": str(exc), "raw_final": generation_raw.final_text}
        )
        correction_prompt = generator_prompt(
            envelope,
            """上一次输出因合同非法已被代码拒绝，未形成歌词版本，也未发送给下游。
请重新执行同一个第4行定点返修。必须把 SUMMARY: 单独放一行，摘要放在下一行；
必须把 LYRIC: 单独放一行，歌词从再下一行开始。不得使用 SUMMARY:摘要 这种同行写法。""",
        )
        generation_raw, correction_events = await invoke(
            runner, profile, "generator", "NEG-PH009-L4__generator", correction_prompt
        )
        for kind, count in correction_events.items():
            generation_events[kind] = generation_events.get(kind, 0) + count
        generation = parse_generation_result(generation_raw.final_text)
    repair_gate = validate_local_repair(before, generation.lyric, [4])
    hard_gate = validate_canonical(generation.lyric, GOLDEN)

    review2_prompt = reviewer_prompt(
        {"case_id": "NEG-PH009-L4", "content_version": 2, "golden_line": GOLDEN},
        generation.lyric.text,
    )
    review2_raw, review2_events = await invoke(
        runner, profile, "reviewer", "NEG-PH009-L4__reviewer__v2", review2_prompt
    )
    review2 = parse_review_result(review2_raw.final_text)
    report = {
        "case_id": "NEG-PH009-L4",
        "fixture_scope_used_for_repair": True,
        "real_review_detection_hit": detection_hit,
        "review_v1_session": review1_raw.session_id,
        "review_v2_session": review2_raw.session_id,
        "review_sessions_changed": review1_raw.session_id != review2_raw.session_id,
        "generator_session": generation_raw.session_id,
        "generation_contract_failures": generation_contract_failures,
        "review_v1": asdict(review1),
        "review_v2": asdict(review2),
        "before_lyric": V1,
        "after_lyric": generation.lyric.text,
        "repair_gate": repair_gate,
        "hard_gate": hard_gate,
        "event_counts": {
            "review_v1": review1_events,
            "generation": generation_events,
            "review_v2": review2_events,
        },
    }
    output = ROOT / "reports" / "NEG-PH009-L4_real.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not (
        repair_gate["locked_lines_unchanged"]
        and repair_gate["changed_lines"] == [4]
        and hard_gate["pass"]
        and report["review_sessions_changed"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
