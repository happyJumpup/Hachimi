import pytest
from pydantic import SecretStr, ValidationError

from hakimi_analysis.settings import Settings


def test_test_provider_is_rejected_outside_test_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="development", analysis_provider="test")


def test_test_provider_is_allowed_only_in_test_environment() -> None:
    settings = Settings(app_env="test", analysis_provider="test")
    assert settings.analysis_provider == "test"


def test_secret_values_are_redacted_from_settings_repr() -> None:
    settings = Settings(
        app_env="development",
        analysis_provider="cloud",
        ark_api_key=SecretStr("ark-secret-value"),
        volc_asr_api_key=SecretStr("asr-secret-value"),
    )
    representation = repr(settings)
    assert "ark-secret-value" not in representation
    assert "asr-secret-value" not in representation
