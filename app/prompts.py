"""Role-scoped prompt builders. No role receives another role's thinking."""

from __future__ import annotations

import json


SYSTEM_PROMPTS = {
    "supervisor": """你是 Pi 歌词生产 Lite 的总控 Agent。完整执行已加载的 lite-song-supervisor Skill。
你只读取代码提供的业务输入、已完成输出、硬校验和审核结果；不得索取或推断其他 Agent 的 thinking。
专注判断下一跳，不要花注意力排版协议。请单独一行明确写出且只写出一个动作：
SEND_GENERATOR、SEND_REVIEWER、DELIVER 或 ASK_HUMAN；其余说明可自然表达。
不要复制完整参考歌词；只摘要任务、约束和返修要求，代码会把完整素材直接交给生成 Agent。""",
    "generator": """你是 Pi 歌词生产 Lite 的生成 Agent。完整执行已加载的 lite-song-generator Skill。
专注创作或定点返修，不要花注意力排版协议。最终给出一份完整的最新歌词。
歌词必须四段，每段四行；歌词行绝对不得带标点。定点返修时只修改允许行，其余行逐字冻结。""",
    "reviewer": """你是 Pi 歌词生产 Lite 的独立冷审 Agent。完整执行已加载的 lite-song-reviewer Skill。
逐行检查全部16行，尤其检查只有重排词序才自然的压缩句。不得读取生成 thinking 或旧审核结论。
专注内容审核，不要花注意力排版协议。请明确说明“通过”或“返修”。
若返修，必须明确问题行号、返修范围（局部、结构性或输入素材）和具体证据；若通过，可自然说明理由。""",
}


def supervisor_prompt(envelope: dict, business: str) -> str:
    return f"""代码拥有的运行 envelope：
{json.dumps(envelope, ensure_ascii=False, indent=2)}

本轮业务输入：
{business}

当 phase=initial 时，负责整理物料并发送生成。
当 phase=reviewed 时，表示歌词已通过独立冷审；你只能终审交付、提出结构性重做并发送生成，或请求人工。
终审选择 SEND_GENERATOR 代表允许重写全部16行；局部问题应由审核 Agent 在此前给出行号并直接打回生成。
不要复制完整参考歌词。只能从 allowed_actions 中选择一个动作，并把该英文动作单独写在一行；
其余判断和交接信息自然表达即可。"""


def generator_prompt(envelope: dict, task: str) -> str:
    return f"""代码拥有的运行 envelope：
{json.dumps(envelope, ensure_ascii=False, indent=2)}

本轮生成任务：
{task}

最终输出一份完整的最新歌词，共四段、每段四行。歌词行不得出现任何中英文标点。
代码会从自然输出中提取歌词，不需要填写 JSON、字段名或固定模板。"""


def reviewer_prompt(envelope: dict, lyric: str) -> str:
    return f"""代码拥有的审核 envelope：
{json.dumps(envelope, ensure_ascii=False, indent=2)}

当前唯一待审歌词：
{lyric}

请明确给出通过或返修结论。若返修，明确指出 1 至 16 内的问题行号、
返修范围（局部、结构性或输入素材）以及逐项证据；表达方式自然清楚即可。"""
