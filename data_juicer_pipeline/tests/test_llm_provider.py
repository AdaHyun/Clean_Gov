from pathlib import Path

import pytest

from llm_provider import (
    NO_AUTH_API_KEY_PLACEHOLDER,
    inspect_llm_settings,
    resolve_llm_provider,
)


def _write_config(path: Path) -> None:
    path.write_text(
        """default_provider: company
providers:
  company:
    enabled: true
    external: false
    api_style: openai_compatible
    base_url_env: COMPANY_URL
    model_env: COMPANY_MODEL
    api_key_env: COMPANY_KEY
  siliconflow:
    enabled: true
    external: true
    api_style: openai_compatible
    base_url: https://api.siliconflow.cn/v1
    model_env: SILICON_MODEL
    api_key_env: SILICON_KEY
""",
        encoding="utf-8",
    )


def test_company_provider_uses_local_env_file_without_exposing_key(tmp_path):
    config = tmp_path / "providers.yaml"
    env_file = tmp_path / ".env.local"
    _write_config(config)
    env_file.write_text(
        "COMPANY_URL=https://company.example/v1\n"
        "COMPANY_MODEL=company-model\n"
        "COMPANY_KEY=top-secret\n",
        encoding="utf-8",
    )

    status = inspect_llm_settings(config, env_file)
    assert status["provider"] == "company"
    assert status["ready"] is True
    assert "top-secret" not in repr(status)

    provider = resolve_llm_provider(
        config,
        env_file,
        requested_provider=None,
        data_classification="restricted",
        allow_external_llm=False,
    )
    assert provider.model == "company-model"
    assert provider.child_environment() == {
        "OPENAI_API_KEY": "top-secret",
        "OPENAI_BASE_URL": "https://company.example/v1",
    }


def test_missing_company_values_report_variable_names_not_secret(tmp_path):
    config = tmp_path / "providers.yaml"
    env_file = tmp_path / ".env.local"
    _write_config(config)
    env_file.write_text("COMPANY_KEY=\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        resolve_llm_provider(
            config,
            env_file,
            requested_provider="company",
            data_classification="restricted",
            allow_external_llm=False,
        )
    message = str(error.value)
    assert "COMPANY_URL" in message
    assert "COMPANY_MODEL" in message
    assert "COMPANY_KEY" in message


def test_company_provider_can_explicitly_disable_api_key_requirement(tmp_path):
    config = tmp_path / "providers.yaml"
    env_file = tmp_path / ".env.local"
    config.write_text(
        """default_provider: company
providers:
  company:
    enabled: true
    external: false
    api_style: openai_compatible
    base_url_env: COMPANY_URL
    model_env: COMPANY_MODEL
    api_key_env: COMPANY_KEY
    api_key_required: false
""",
        encoding="utf-8",
    )
    env_file.write_text(
        "COMPANY_URL=http://10.61.5.9:7005/v1\n"
        "COMPANY_MODEL=1\n"
        "COMPANY_KEY=\n",
        encoding="utf-8",
    )

    status = inspect_llm_settings(config, env_file)
    assert status["ready"] is True
    assert status["api_key_required"] is False
    assert status["api_key_configured"] is False

    provider = resolve_llm_provider(
        config,
        env_file,
        requested_provider=None,
        data_classification="restricted",
        allow_external_llm=False,
    )
    assert provider.api_key == ""
    assert provider.api_key_required is False
    assert provider.child_environment() == {
        "OPENAI_API_KEY": NO_AUTH_API_KEY_PLACEHOLDER,
        "OPENAI_BASE_URL": "http://10.61.5.9:7005/v1",
    }


def test_external_provider_is_blocked_for_non_public_data(tmp_path):
    config = tmp_path / "providers.yaml"
    env_file = tmp_path / ".env.local"
    _write_config(config)
    env_file.write_text(
        "SILICON_MODEL=test-model\nSILICON_KEY=secret\n",
        encoding="utf-8",
    )

    with pytest.raises(PermissionError, match="禁止发送"):
        resolve_llm_provider(
            config,
            env_file,
            requested_provider="siliconflow",
            data_classification="internal",
            allow_external_llm=True,
        )


def test_external_provider_requires_public_and_explicit_opt_in(tmp_path):
    config = tmp_path / "providers.yaml"
    env_file = tmp_path / ".env.local"
    _write_config(config)
    env_file.write_text(
        "SILICON_MODEL=test-model\nSILICON_KEY=secret\n",
        encoding="utf-8",
    )

    with pytest.raises(PermissionError, match="--allow-external-llm"):
        resolve_llm_provider(
            config,
            env_file,
            requested_provider="siliconflow",
            data_classification="public",
            allow_external_llm=False,
        )

    provider = resolve_llm_provider(
        config,
        env_file,
        requested_provider="siliconflow",
        data_classification="public",
        allow_external_llm=True,
    )
    assert provider.external is True
