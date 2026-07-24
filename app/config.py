"""Static Lite profile loading and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


Role = Literal["supervisor", "generator", "reviewer"]


@dataclass(frozen=True, slots=True)
class RoleProfile:
    model: str
    thinking: str
    skill: str
    persistent_session: bool


@dataclass(frozen=True, slots=True)
class LiteProfile:
    profile_version: str
    name: str
    roles: dict[Role, RoleProfile]


def load_profile(path: Path) -> LiteProfile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    roles: dict[Role, RoleProfile] = {}
    expected = {"supervisor", "generator", "reviewer"}
    if set(raw.get("roles", {})) != expected:
        raise RuntimeError("风格档案必须且只能配置 supervisor/generator/reviewer")
    for role, item in raw["roles"].items():
        model = str(item.get("model", "")).strip()
        thinking = str(item.get("thinking", "")).strip()
        skill = str(item.get("skill", "")).strip()
        if "/" not in model or thinking not in {
            "off",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise RuntimeError(f"{role} 模型或 thinking 配置无效")
        if not skill:
            raise RuntimeError(f"{role} Skill 配置为空")
        roles[role] = RoleProfile(
            model=model,
            thinking=thinking,
            skill=skill,
            persistent_session=bool(item.get("persistent_session")),
        )
    if roles["reviewer"].persistent_session:
        raise RuntimeError("reviewer 必须按歌词版本冷启动")
    if not roles["supervisor"].persistent_session or not roles["generator"].persistent_session:
        raise RuntimeError("supervisor/generator 必须在 Case 内保持 Session")
    return LiteProfile(
        profile_version=str(raw["profile_version"]),
        name=str(raw["name"]),
        roles=roles,
    )

