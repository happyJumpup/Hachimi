import pytest
from pydantic import SecretStr, ValidationError

from hakimi_analysis.settings import Settings


def test_test_provider_is_rejected_outside_test_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="development", analysis_provider="test")


def test_test_provider_is_allowed_only_in_test_environment() -> None:
    settings = Settings(_env_file=None, app_env="test", analysis_provider="test")
    assert settings.analysis_provider == "test"


def test_analysis_capacity_has_safe_production_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.judge_analysis_concurrency == 2
    assert settings.public_analysis_concurrency == 0


def test_secret_values_are_redacted_from_settings_repr() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        analysis_provider="cloud",
        ark_api_key=SecretStr("ark-secret-value"),
        volc_asr_api_key=SecretStr("asr-secret-value"),
        judge_access_code=SecretStr("judge-secret-value"),
        access_cookie_secret=SecretStr("cookie-secret-value"),
    )
    representation = repr(settings)
    assert "ark-secret-value" not in representation
    assert "asr-secret-value" not in representation
    assert "judge-secret-value" not in representation
    assert "cookie-secret-value" not in representation
