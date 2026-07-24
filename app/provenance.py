"""Fail-closed startup provenance and private runtime directory checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .config import LiteProfile


LITE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LITE_ROOT
SUPERVISOR_VALIDATOR = LITE_ROOT / "app" / "validator.py"
OUTPUT_ADAPTER = LITE_ROOT / "app" / "contracts.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_private_runtime_dir(configured: str | None = None) -> Path:
    path = (
        Path(configured).expanduser()
        if configured
        else Path.home() / "Library" / "Application Support" / "PiSwimlaneLite" / "runs"
    )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o700 or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError(f"运行目录必须为当前用户私有可写目录（0700）：{path}")
    probe = path / ".write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return path.resolve()


def _run(*args: str) -> str:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() or result.stderr.strip()


def collect_provenance(profile_path: Path, profile: LiteProfile) -> dict:
    pi_binary = shutil.which("pi")
    if not pi_binary:
        raise RuntimeError("找不到 Pi CLI")
    pi_version = _run(pi_binary, "--version")
    commit = _run("git", "rev-parse", "HEAD")
    dirty = bool(_run("git", "status", "--porcelain"))
    if not SUPERVISOR_VALIDATOR.is_file():
        raise RuntimeError(f"找不到 Lite Validator：{SUPERVISOR_VALIDATOR}")

    skill_hashes: dict[str, str] = {}
    for role, role_profile in profile.roles.items():
        skill_file = LITE_ROOT / "skills" / role_profile.skill / "SKILL.md"
        if not skill_file.is_file():
            raise RuntimeError(f"找不到 {role} Lite Skill：{skill_file}")
        skill_hashes[role] = sha256_file(skill_file)

    return {
        "schema_version": "provenance_v1",
        "pi_binary": pi_binary,
        "pi_version": pi_version,
        "git_commit": commit,
        "git_dirty": dirty,
        "profile_version": profile.profile_version,
        "profile_sha256": sha256_file(profile_path),
        "validator_path": str(SUPERVISOR_VALIDATOR),
        "validator_sha256": sha256_file(SUPERVISOR_VALIDATOR),
        "output_adapter": "middleware_semantic_v1",
        "output_adapter_sha256": sha256_file(OUTPUT_ADAPTER),
        "skill_sha256": skill_hashes,
    }


def write_provenance(path: Path, provenance: dict) -> None:
    path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
