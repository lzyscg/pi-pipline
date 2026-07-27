"""Safe Pi model discovery for the local Case creation surface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

PROVIDER_ENV_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"),
    "ant-ling": ("ANT_LING_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "azure-openai": ("AZURE_OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "google": ("GEMINI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "vercel-ai-gateway": ("AI_GATEWAY_API_KEY",),
    "zai": ("ZAI_API_KEY", "ZAI_CODING_CN_API_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "opencode": ("OPENCODE_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
    "kimi": ("KIMI_API_KEY",),
    "cloudflare": ("CLOUDFLARE_API_KEY",),
    "qwen": ("QWEN_TOKEN_PLAN_API_KEY", "QWEN_TOKEN_PLAN_CN_API_KEY"),
    "xiaomi": (
        "XIAOMI_API_KEY",
        "XIAOMI_TOKEN_PLAN_CN_API_KEY",
        "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
        "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
    ),
}


@dataclass(frozen=True, slots=True)
class ModelOption:
    provider: str
    model: str
    context: str
    max_output: str
    supports_thinking: bool
    supports_images: bool

    @property
    def model_id(self) -> str:
        return f"{self.provider}/{self.model}"

    def public(self, configured_providers: set[str]) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_id": self.model_id,
            "thinking": self.supports_thinking,
            "configured": self.provider in configured_providers,
        }


def parse_model_list(output: str) -> list[ModelOption]:
    result: list[ModelOption] = []
    for raw_line in output.splitlines():
        columns = raw_line.split()
        if len(columns) != 6 or columns[0].lower() == "provider":
            continue
        provider, model, context, max_output, thinking, images = columns
        if thinking not in {"yes", "no"} or images not in {"yes", "no"}:
            continue
        result.append(
            ModelOption(
                provider=provider,
                model=model,
                context=context,
                max_output=max_output,
                supports_thinking=thinking == "yes",
                supports_images=images == "yes",
            )
        )
    if not result:
        raise RuntimeError("Pi 未返回可解析的模型目录")
    return result


def configured_provider_names(
    auth_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> set[str]:
    path = auth_path or Path.home() / ".pi" / "agent" / "auth.json"
    environment = os.environ if environ is None else environ
    configured: set[str] = set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            configured.update(str(name) for name, value in raw.items() if isinstance(value, dict))
    except (OSError, json.JSONDecodeError):
        pass
    for provider, keys in PROVIDER_ENV_KEYS.items():
        if any(environment.get(key) for key in keys):
            configured.add(provider)
    return configured


def _run_pi_models(command: Sequence[str]) -> str:
    completed = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout


class PiModelCatalog:
    def __init__(
        self,
        *,
        pi_binary: str | None = None,
        auth_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        runner: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.pi_binary = pi_binary or shutil.which("pi") or "pi"
        self.auth_path = auth_path
        self.environ = environ
        self.runner = runner or _run_pi_models

    def snapshot(self) -> dict:
        options = parse_model_list(self.runner([self.pi_binary, "--list-models"]))
        configured = configured_provider_names(self.auth_path, self.environ)
        visible_configured = sorted({item.provider for item in options} & configured)
        return {
            "models": [item.public(configured) for item in options],
            "configured_providers": visible_configured,
            "thinking_levels": list(THINKING_LEVELS),
        }
