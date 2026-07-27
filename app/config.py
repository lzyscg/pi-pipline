"""Static Lite profile loading and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .model_catalog import THINKING_LEVELS


Role = Literal["supervisor", "generator", "reviewer"]
ROLES: tuple[Role, ...] = ("supervisor", "generator", "reviewer")


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
    expected = set(ROLES)
    if set(raw.get("roles", {})) != expected:
        raise RuntimeError("风格档案必须且只能配置 supervisor/generator/reviewer")
    for role, item in raw["roles"].items():
        model = str(item.get("model", "")).strip()
        thinking = str(item.get("thinking", "")).strip()
        skill = str(item.get("skill", "")).strip()
        if "/" not in model or thinking not in THINKING_LEVELS:
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


def public_role_config(role_profiles: dict[Role, RoleProfile]) -> dict:
    return {
        role: {
            "model": role_profiles[role].model,
            "thinking": role_profiles[role].thinking,
        }
        for role in ROLES
    }


def role_profiles_from_selection(
    base: LiteProfile,
    raw: dict | None,
    catalog: dict | None,
    *,
    require_available: bool,
) -> tuple[dict[Role, RoleProfile], str]:
    if raw is None:
        return (
            {
                role: RoleProfile(
                    model=base.roles[role].model,
                    thinking=base.roles[role].thinking,
                    skill=base.roles[role].skill,
                    persistent_session=base.roles[role].persistent_session,
                )
                for role in ROLES
            },
            "default",
        )
    if set(raw) != set(ROLES):
        raise RuntimeError("模型配置必须且只能包含三个 Agent")
    if require_available and catalog is None:
        raise RuntimeError("Pi 模型目录不可用")
    catalog_models = {
        str(item.get("model_id")): item
        for item in (catalog or {}).get("models", [])
        if isinstance(item, dict)
    }
    result: dict[Role, RoleProfile] = {}
    for role in ROLES:
        selected = raw.get(role)
        if not isinstance(selected, dict) or set(selected) != {"model", "thinking"}:
            raise RuntimeError(f"{role} 模型配置字段无效")
        model = str(selected["model"]).strip()
        thinking = str(selected["thinking"]).strip()
        if "/" not in model or thinking not in THINKING_LEVELS:
            raise RuntimeError(f"{role} 模型或 thinking 配置无效")
        if require_available:
            option = catalog_models.get(model)
            if option is None:
                raise RuntimeError(f"{role} 模型不可用：{model}")
            if not option.get("configured"):
                raise RuntimeError(f"{role} provider 未配置：{model.split('/', 1)[0]}")
            if not option.get("thinking") and thinking != "off":
                raise RuntimeError(f"{role} 模型不支持 thinking")
        base_role = base.roles[role]
        result[role] = RoleProfile(
            model=model,
            thinking=thinking,
            skill=base_role.skill,
            persistent_session=base_role.persistent_session,
        )
    return result, "case"
