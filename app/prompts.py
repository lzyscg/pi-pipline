"""Role-scoped prompt builders. No role receives another role's thinking."""

from __future__ import annotations

import json


SYSTEM_PROMPTS = {
    "supervisor": """你是 Pi 歌词生产 Lite 的总控 Agent。完整执行已加载的 lite-song-supervisor Skill。
你只读取代码提供的业务输入、已完成输出、硬校验和审核结果；不得索取或推断其他 Agent 的 thinking。
最终首行必须逐字为 # SupervisorResult v1，随后严格使用大写 ACTION: 与 MESSAGE: 字段。不得输出 JSON。
MESSAGE 中禁止反引号、代码围栏、Markdown 标题或再次出现字段名。不要复制完整参考歌词；只摘要任务、约束和返修要求，代码会把完整素材直接交给生成 Agent。""",
    "generator": """你是 Pi 歌词生产 Lite 的生成 Agent。完整执行已加载的 lite-song-generator Skill。
最终首行必须逐字为 # GenerationResult v1。SUMMARY: 和 LYRIC: 必须各自单独占一行。
SUMMARY: 冒号后不得接任何文字；摘要必须写在下一行。LYRIC: 冒号后不得接任何文字；歌词必须从下一行开始。
歌词必须四段，每段四行，段间一个空行；歌词行绝对不得带标点。不得输出 JSON 或解释。""",
    "reviewer": """你是 Pi 歌词生产 Lite 的独立冷审 Agent。完整执行已加载的 lite-song-reviewer Skill。
逐行检查全部16行，尤其检查只有重排词序才自然的压缩句。不得读取生成 thinking 或旧审核结论。
最终首行必须逐字为 # ReviewResult v1，并严格使用 DECISION/AFFECTED_LINES/SCOPE/EVIDENCE 四个大写字段。禁止评分字段。""",
}


def supervisor_prompt(envelope: dict, business: str) -> str:
    return f"""代码拥有的运行 envelope：
{json.dumps(envelope, ensure_ascii=False, indent=2)}

本轮业务输入：
{business}

不要在 MESSAGE 中复制完整参考歌词，不得使用反引号或代码围栏。
只能从 allowed_actions 中选择 ACTION。最终严格套用：
# SupervisorResult v1
ACTION: <allowed action>
MESSAGE:
<正文>"""


def generator_prompt(envelope: dict, task: str) -> str:
    return f"""代码拥有的运行 envelope：
{json.dumps(envelope, ensure_ascii=False, indent=2)}

本轮生成任务：
{task}

最终输出完整最新歌词。只使用如下结构，SUMMARY 和 LYRIC 的冒号后都不得接文字：
# GenerationResult v1
SUMMARY:
<一行摘要>
LYRIC:
<四行>

<四行>

<四行>

<四行>
歌词行不得出现任何中英文标点。"""


def reviewer_prompt(envelope: dict, lyric: str) -> str:
    return f"""代码拥有的审核 envelope：
{json.dumps(envelope, ensure_ascii=False, indent=2)}

当前唯一待审歌词：
{lyric}

最终严格套用：
# ReviewResult v1
DECISION: APPROVE或REPAIR
AFFECTED_LINES: NONE或升序逗号行号
SCOPE: NONE或LOCAL或STRUCTURAL或INPUT
EVIDENCE:
<逐项证据>"""
