"""Load LLM provider settings without leaking secrets into run artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DATA_CLASSIFICATIONS = ("restricted", "internal", "public")
NO_AUTH_API_KEY_PLACEHOLDER = "data-juicer-no-auth"


@dataclass(frozen=True)
class LLMProvider:
    name: str
    external: bool
    base_url: str
    model: str
    api_key_env: str
    api_key: str
    api_key_required: bool
    endpoint: str
    response_path: str
    sampling_params: dict[str, Any]

    def child_environment(self) -> dict[str, str]:
        """Return only the compatibility variables needed by Data-Juicer."""
        return {
            # openai.OpenAI requires a non-empty client option even when the
            # compatible server has authentication disabled. This value is not
            # a credential and is never written to generated configs or logs.
            "OPENAI_API_KEY": self.api_key or NO_AUTH_API_KEY_PLACEHOLDER,
            "OPENAI_BASE_URL": self.base_url,
        }


def load_env_values(path: Path) -> dict[str, str]:
    """Read a small dotenv-style file; environment variables take precedence later."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_number} 不是 KEY=VALUE 格式")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"{path}:{line_number} 环境变量名为空")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _read_provider_file(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"LLM 提供商配置不存在: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("providers"), dict):
        raise ValueError(f"LLM 提供商配置缺少 providers: {path}")
    default_provider = str(raw.get("default_provider") or "").strip()
    if not default_provider:
        raise ValueError(f"LLM 提供商配置缺少 default_provider: {path}")
    return default_provider, raw["providers"]


def _setting(
    provider: Mapping[str, Any],
    name: str,
    env_values: Mapping[str, str],
) -> str:
    literal = str(provider.get(name) or "").strip()
    env_name = str(provider.get(f"{name}_env") or "").strip()
    if env_name:
        return str(os.environ.get(env_name, env_values.get(env_name, ""))).strip()
    return literal


def inspect_llm_settings(
    config_path: Path,
    env_path: Path,
    requested_provider: str | None = None,
) -> dict[str, Any]:
    """Return readiness booleans only; never return credentials or secret values."""
    default_provider, providers = _read_provider_file(config_path)
    name = requested_provider or default_provider
    if name not in providers:
        raise ValueError(f"未知 LLM 提供商 {name!r}; 可选 {sorted(providers)}")
    raw = providers[name]
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 提供商 {name!r} 配置不是对象")
    env_values = load_env_values(env_path)
    api_key_env = str(raw.get("api_key_env") or "").strip()
    api_key_required = bool(raw.get("api_key_required", True))
    has_key = bool(api_key_env and os.environ.get(api_key_env, env_values.get(api_key_env, "")).strip())
    status = {
        "provider": name,
        "default_provider": default_provider,
        "enabled_by_config": bool(raw.get("enabled", True)),
        "external": bool(raw.get("external", False)),
        "api_style": str(raw.get("api_style") or ""),
        "base_url_configured": bool(_setting(raw, "base_url", env_values)),
        "model_configured": bool(_setting(raw, "model", env_values)),
        "api_key_configured": has_key,
        "api_key_required": api_key_required,
        "api_key_env": api_key_env,
        "env_file": str(env_path.resolve()),
    }
    status["ready"] = bool(
        status["enabled_by_config"]
        and status["base_url_configured"]
        and status["model_configured"]
        and (status["api_key_configured"] or not status["api_key_required"])
    )
    return status


def resolve_llm_provider(
    config_path: Path,
    env_path: Path,
    *,
    requested_provider: str | None,
    data_classification: str,
    allow_external_llm: bool,
) -> LLMProvider:
    """Resolve one provider and enforce the outbound-data policy."""
    if data_classification not in DATA_CLASSIFICATIONS:
        raise ValueError(
            f"未知数据保密级别 {data_classification!r}; 可选 {list(DATA_CLASSIFICATIONS)}"
        )
    default_provider, providers = _read_provider_file(config_path)
    name = requested_provider or default_provider
    if name not in providers:
        raise ValueError(f"未知 LLM 提供商 {name!r}; 可选 {sorted(providers)}")
    raw = providers[name]
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 提供商 {name!r} 配置不是对象")
    if not bool(raw.get("enabled", True)):
        raise ValueError(f"LLM 提供商 {name!r} 已在配置中禁用")
    if str(raw.get("api_style") or "") != "openai_compatible":
        raise ValueError(f"LLM 提供商 {name!r} 不是受支持的 OpenAI 兼容接口")

    external = bool(raw.get("external", False))
    if external and data_classification != "public":
        raise PermissionError(
            f"数据保密级别为 {data_classification}，禁止发送到外部提供商 {name}"
        )
    if external and not allow_external_llm:
        raise PermissionError(
            f"外部提供商 {name} 需要同时指定 --data-classification public "
            "和 --allow-external-llm"
        )

    env_values = load_env_values(env_path)
    base_url = _setting(raw, "base_url", env_values).rstrip("/")
    model = _setting(raw, "model", env_values)
    api_key_env = str(raw.get("api_key_env") or "").strip()
    api_key_required = bool(raw.get("api_key_required", True))
    api_key = str(os.environ.get(api_key_env, env_values.get(api_key_env, ""))).strip()
    missing: list[str] = []
    if not base_url:
        missing.append(str(raw.get("base_url_env") or "base_url"))
    if not model:
        missing.append(str(raw.get("model_env") or "model"))
    if api_key_required and not api_key:
        missing.append(api_key_env or "api_key_env")
    if missing:
        raise ValueError(
            f"LLM 提供商 {name!r} 尚未配置完成；请填写 {env_path}: {', '.join(missing)}"
        )

    sampling_params = raw.get("sampling_params") or {}
    if not isinstance(sampling_params, dict):
        raise ValueError(f"LLM 提供商 {name!r} sampling_params 不是对象")
    return LLMProvider(
        name=name,
        external=external,
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        api_key=api_key,
        api_key_required=api_key_required,
        endpoint=str(raw.get("endpoint") or "/chat/completions"),
        response_path=str(raw.get("response_path") or "choices.0.message.content"),
        sampling_params=dict(sampling_params),
    )
